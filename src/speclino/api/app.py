"""FastAPI アプリケーションのファクトリ（役割 web / worker / all で公開するルートを分ける）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from speclino.api.deps import AppState, build_state
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

    app = FastAPI(title="SpecLino", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "role": settings.role}

    if settings.role in ("web", "all"):
        app.include_router(webhooks.router)
        app.include_router(review_runs.router)
        if WEB_DIST.is_dir():
            app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    if settings.role in ("worker", "all"):
        app.include_router(tasks.router)
    return app


# uvicorn からモジュールレベルの ASGI アプリとして参照するためのラッパ。
app = create_app(Settings())
