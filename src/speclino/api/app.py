"""FastAPI アプリケーションのファクトリ（役割 web / worker / all で公開するルートを分ける）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Scope

from speclino.a2a_server.mount import mount_a2a
from speclino.api.deps import AppState, build_state
from speclino.api.errors import error_responses, install_error_handlers
from speclino.api.routes import (
    attestations,
    internal_mcp,
    knowledge,
    mcp_tokens,
    me,
    members,
    policies,
    project_settings,
    projects,
    repositories,
    review_runs,
    spec_preview,
    sync,
    tasks,
    webhooks,
)
from speclino.mcp_server.server import MCP_PATH, mount_mcp
from speclino.sandbox_runner import probe as sandbox_probe
from speclino.sandbox_runner import runner as sandbox_runner
from speclino.settings import Settings

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
# 静的配信の SPA フォールバックを適用しないパス（API は JSON の 404 を返す）
API_PREFIXES = ("api/", "webhooks/", "internal/", "mcp", "a2a", ".well-known")


def create_app(settings: Settings, state: AppState | None = None) -> FastAPI:
    """設定を受け取り FastAPI アプリを構築する。state を渡せばテストで差し替えられる。"""

    # MCP は web / all で常にマウントする。使えるのは DB に有効なトークンがあるときだけ（S54）
    mcp_app, mcp_session_manager = (
        mount_mcp(lambda: app.state.speclino, settings.public_url)
        if settings.role in ("web", "all")
        else (None, None)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.speclino = state or build_state(settings)
        if mcp_session_manager is not None:
            # マウントした MCP アプリの lifespan は走らないので、親の lifespan で session
            # manager を動かす（mcp SDK の「Add to an existing app」の要件）。session manager は
            # anyio のタスクグループを持ち、入ったタスクと同じタスクで出る必要がある。lifespan の
            # 開始と終了が別のタスクになる呼び出し方（pytest の fixture など）でも壊れないよう、
            # 専用のタスクの中で開始から終了までを完結させる。
            started, stop = asyncio.Event(), asyncio.Event()

            async def run_mcp() -> None:
                async with mcp_session_manager():
                    started.set()
                    await stop.wait()

            runner = asyncio.create_task(run_mcp())
            waiter = asyncio.create_task(started.wait())
            await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            if runner.done():
                # 開始する前に落ちた。待ち続けずに例外を出して起動を失敗させる
                runner.result()
                raise RuntimeError("MCP の session manager が開始前に終了した")
            try:
                yield
            finally:
                stop.set()
                await runner
        else:
            yield
        await app.state.speclino.engine.dispose()

    # 入力検証エラー（422）も ErrorResponse で返す（API-ERR-001）。個別のエラーは各ルートで宣言する
    app = FastAPI(title="SpecLino", lifespan=lifespan, responses=error_responses(422))
    install_error_handlers(app)

    # `/healthz` は使えない: Cloud Run の Google Frontend が横取りし、コンテナまで届かない
    # （他のパスは届く。ログにもリクエストが残らない）。
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "role": settings.role}

    if settings.role in ("web", "all"):
        app.include_router(webhooks.router)
        app.include_router(review_runs.router)
        app.include_router(me.router)
        app.include_router(mcp_tokens.router)
        app.include_router(repositories.router)
        app.include_router(projects.router)
        app.include_router(policies.router)
        app.include_router(knowledge.router)
        app.include_router(attestations.router)
        app.include_router(spec_preview.router)
        app.include_router(sync.router)
        app.include_router(sync.bundle_router)
        app.include_router(project_settings.router)
        app.include_router(members.router)
        app.include_router(members.accept_router)
    if settings.role in ("worker", "all"):
        app.include_router(tasks.router)
        app.include_router(internal_mcp.router)
    # sandbox ロールは Cloud Run sandboxes の疎通確認（probe）と本実装（runner）を持つ（S38）。
    # 呼べるのは worker SA のみ（Cloud Run の IAM で制限）。
    if settings.role == "sandbox":
        app.include_router(sandbox_probe.router)
        app.include_router(sandbox_runner.router)
    if mcp_app is not None:
        app.router.routes.append(Route(MCP_PATH, endpoint=mcp_app, name="mcp"))
    # A2A（S59）: Agent Card と `/a2a`。state はリクエストのときに引く（ここではまだ無い）
    if settings.a2a_enabled and settings.role in ("web", "all"):
        mount_a2a(app, settings, lambda: app.state.speclino)
    # 静的配信の mount は "/" 配下を全て受けるので、全ルーターの後に置く（先に置くと
    # role=all のとき /internal/tasks が 405 になる）
    if settings.role in ("web", "all") and WEB_DIST.is_dir():
        app.mount("/", SpaStaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


class SpaStaticFiles(StaticFiles):
    """SPA 用: 存在しないパス（例 /runs/<id> のディープリンク）は index.html を返し、
    クライアント側ルーターに委ねる。API は先にルーターが受けるのでここには来ない。

    index.html は毎回確認させる（no-cache）。キャッシュの指定が無いとブラウザが Last-Modified
    から推測して古い index.html を使い続け、デプロイ後も古いバンドルを読む（S54 の本番で、
    スマホで新しい画面のリンクが出なかった）。assets はファイル名にハッシュが入り、中身が変われば
    名前も変わるので指定しない。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as e:
            is_api = path.startswith(API_PREFIXES)
            if e.status_code != 404 or scope["method"] not in ("GET", "HEAD") or is_api:
                raise
            response = await super().get_response("index.html", scope)
        if not path.startswith("assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


# uvicorn からモジュールレベルの ASGI アプリとして参照するためのラッパ。
app = create_app(Settings())
