"""FastAPI の応答・例外を outcome へ写す明示アダプター（理解する構文だけに限定）。

`raise HTTPException(status_code=<int>)` と `return Response(status_code=<int>)` だけを
http_response として認識する。それ以外はサービス層の戻り値（return）または例外として扱う。
"""

from __future__ import annotations

import ast

from speclino.domain.code_ir import Outcome

RESPONSE_SYMBOLS = frozenset(
    {
        "fastapi.Response",
        "fastapi.responses.Response",
        "fastapi.responses.JSONResponse",
        "fastapi.responses.PlainTextResponse",
        "starlette.responses.Response",
        "starlette.responses.JSONResponse",
    }
)
EXCEPTION_SYMBOLS = frozenset({"fastapi.HTTPException", "starlette.exceptions.HTTPException"})


def _status_kw(call: ast.Call) -> int | None:
    for kw in call.keywords:
        if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, int) and not isinstance(kw.value.value, bool):
                return kw.value.value
            return None
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, int):
        return call.args[0].value
    return None


def outcome_of_raise(call: ast.Call, callee: str) -> Outcome | None:
    """`raise HTTPException(status_code=N)` を認識する。認識できなければ None。"""
    if callee not in EXCEPTION_SYMBOLS:
        return None
    status = _status_kw(call)
    if status is None:
        return None
    return Outcome(kind="http_response", status=status)


def outcome_of_return_call(call: ast.Call, callee: str) -> Outcome | None:
    """`return Response(status_code=N)` を認識する。省略時は 200。"""
    if callee not in RESPONSE_SYMBOLS:
        return None
    for kw in call.keywords:
        if kw.arg == "status_code":
            status = _status_kw(call)
            return Outcome(kind="http_response", status=status) if status is not None else None
    return Outcome(kind="http_response", status=200)
