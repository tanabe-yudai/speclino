"""FastAPI アプリケーションのファクトリ（役割 web / worker / all で公開するルートを分ける）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from speclino.api.deps import AppState, build_state
from speclino.api.errors import error_responses, install_error_handlers
from speclino.api.routes import review_runs, tasks, webhooks
from speclino.settings import Settings

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def create_app(settings: Settings, state: AppState | None = None) -> FastAPI:
    """設定を受け取り FastAPI アプリを構築する。state を渡せばテストで差し替えられる。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.speclino = state or build_state(settings)
        yield
        await app.state.speclino.engine.dispose()

    # 入力検証エラー（422）も ErrorResponse で返す（API-ERR-001）。個別のエラーは各ルートで宣言する
    app = FastAPI(title="SpecLino", lifespan=lifespan, responses=error_responses(422))
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "role": settings.role}

    if settings.role in ("web", "all"):
        app.include_router(webhooks.router)
        app.include_router(review_runs.router)
    if settings.role in ("worker", "all"):
        app.include_router(tasks.router)
    # 静的配信の mount は "/" 配下を全て受けるので、全ルーターの後に置く（先に置くと
    # role=all のとき /internal/tasks が 405 になる）
    if settings.role in ("web", "all") and WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


# uvicorn からモジュールレベルの ASGI アプリとして参照するためのラッパ。
app = create_app(Settings())
