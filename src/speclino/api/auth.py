"""API 認証。dev（local のみ）と Identity Platform（S13 で ID トークン検証を実装）。"""

from __future__ import annotations

from fastapi import Depends, Request

from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error


async def require_user(request: Request, state: AppState = Depends(get_state)) -> str:
    settings = state.settings
    if settings.auth_kind == "dev":
        if settings.env != "local":
            raise api_error(500, "auth_misconfigured", "dev 認証は local 以外で無効")
        return request.headers.get("X-Dev-User", "dev")
    from speclino.adapters.auth.identity_platform import verify_bearer

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise api_error(401, "unauthenticated", "Bearer トークンが必要")
    try:
        user = await verify_bearer(auth.removeprefix("Bearer ").strip(), settings)
    except ValueError as e:
        raise api_error(401, "invalid_token", str(e)) from e
    if settings.allowed_users and user not in settings.allowed_users:
        raise api_error(403, "forbidden", "許可されていない利用者")
    return user
