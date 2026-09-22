"""API エラー応答形式（API-ERR-001、`design/product-spec-summary.md` 規約パッケージ節）。

全てのエラー応答は `{"error": {"code": ..., "message": ...}}`（ErrorResponse）で返す。
ルートは `HTTPException(...)` を直書きせず `raise api_error(...)` を使い、FastAPI の既定
（`{"detail": ...}` / 422 の HTTPValidationError）は `install_error_handlers` で同じ形式に揃える。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def _error_body(code: str, message: str) -> dict[str, Any]:
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()


def api_error(status: int, code: str, message: str) -> HTTPException:
    """`ErrorResponse` 相当の detail を持つ `HTTPException` を返す（呼び出し側で raise する）。"""
    return HTTPException(status_code=status, detail=_error_body(code, message))


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """ルートの `responses=` に渡す openapi 宣言（各状態コードが ErrorResponse を返す）。"""
    return {status: {"model": ErrorResponse} for status in statuses}


def install_error_handlers(app: FastAPI) -> None:
    """HTTPException と入力検証エラーの応答本体を ErrorResponse に統一する。"""

    # FastAPI の HTTPException は Starlette のサブクラス。ルーティング由来の 404 / 405 も
    # Starlette 側で送出されるので、基底クラスに登録して全て同じ形式にする
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            body = detail
        else:
            body = _error_body("http_error", str(detail))
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = _error_body("validation_error", str(exc.errors()))
        return JSONResponse(status_code=422, content=body)


__all__ = ["ErrorBody", "ErrorResponse", "api_error", "error_responses", "install_error_handlers"]
