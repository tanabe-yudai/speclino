"""FastAPI アプリケーションのファクトリ。"""

from __future__ import annotations

from fastapi import FastAPI

from speclino.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    """設定を受け取り FastAPI アプリを構築する。"""
    app = FastAPI(title="SpecLino")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "role": settings.role}

    return app


# uvicorn からモジュールレベルの ASGI アプリとして参照するためのラッパ。
# create_app は settings を必須引数に取るため `--factory` 起動はできない。
app = create_app(Settings())
