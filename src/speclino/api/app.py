"""FastAPI アプリケーションのファクトリ（役割 web / worker / all で公開するルートを分ける）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Scope

from speclino.api.deps import AppState, build_state
from speclino.api.errors import error_responses, install_error_handlers
from speclino.api.routes import review_runs, tasks, webhooks
from speclino.mcp_server.server import MCP_PATH, mount_mcp
from speclino.sandbox_runner import probe as sandbox_probe
from speclino.settings import Settings

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
# 静的配信の SPA フォールバックを適用しないパス（API は JSON の 404 を返す）
API_PREFIXES = ("api/", "webhooks/", "internal/", "mcp")


def create_app(settings: Settings, state: AppState | None = None) -> FastAPI:
    """設定を受け取り FastAPI アプリを構築する。state を渡せばテストで差し替えられる。"""

    mcp_app, mcp_session_manager = (
        mount_mcp(lambda: app.state.speclino, settings.mcp_token, settings.public_url)
        if settings.mcp_token and settings.role in ("web", "all")
        else (None, None)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.speclino = state or build_state(settings)
        if mcp_session_manager is not None:
            # マウントした MCP アプリの lifespan は走らないので、親の lifespan で session
            # manager を動かす（mcp SDK の「Add to an existing app」の要件）
            async with mcp_session_manager():
                yield
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
    if settings.role in ("worker", "all"):
        app.include_router(tasks.router)
    # sandbox ロールは Cloud Run sandboxes の疎通確認だけを持つ（S38）。
    # 呼べるのは worker SA のみ（Cloud Run の IAM で制限）。
    if settings.role == "sandbox":
        app.include_router(sandbox_probe.router)
    if mcp_app is not None:
        app.router.routes.append(Route(MCP_PATH, endpoint=mcp_app, name="mcp"))
    # 静的配信の mount は "/" 配下を全て受けるので、全ルーターの後に置く（先に置くと
    # role=all のとき /internal/tasks が 405 になる）
    if settings.role in ("web", "all") and WEB_DIST.is_dir():
        app.mount("/", SpaStaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


class SpaStaticFiles(StaticFiles):
    """SPA 用: 存在しないパス（例 /runs/<id> のディープリンク）は index.html を返し、
    クライアント側ルーターに委ねる。API は先にルーターが受けるのでここには来ない。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            is_api = path.startswith(API_PREFIXES)
            if e.status_code != 404 or scope["method"] not in ("GET", "HEAD") or is_api:
                raise
            return await super().get_response("index.html", scope)


# uvicorn からモジュールレベルの ASGI アプリとして参照するためのラッパ。
app = create_app(Settings())
