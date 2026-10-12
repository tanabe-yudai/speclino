"""API 認証。dev（local のみ）と Identity Platform（S13 で ID トークン検証を実装）。"""

from __future__ import annotations

from fastapi import Depends, Request

from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error
from speclino.domain.workspace import AuthUser


async def require_user(request: Request, state: AppState = Depends(get_state)) -> AuthUser:
    """認証済みの利用者。見えるものの絞り込みは所属ワークスペースで行う（S53）。

    新規登録は開いているので、ここでは「誰か」を確かめるだけで、何を見られるかは決めない。
    """
    settings = state.settings
    if settings.auth_kind == "dev":
        if settings.env != "local":
            raise api_error(500, "auth_misconfigured", "dev 認証は local 以外で無効")
        # メール・名前は招待の受諾とプロフィールを手元で試すためだけ。local 以外では上で弾いている
        email = request.headers.get("X-Dev-Email")
        return AuthUser(
            uid=request.headers.get("X-Dev-User", "dev"),
            email=email,
            email_verified=email is not None
            and request.headers.get("X-Dev-Email-Verified", "true") == "true",
            name=request.headers.get("X-Dev-Name"),
        )
    from speclino.adapters.auth.identity_platform import verify_bearer

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise api_error(401, "unauthenticated", "Bearer トークンが必要")
    try:
        return await verify_bearer(auth.removeprefix("Bearer ").strip(), settings)
    except ValueError as e:
        raise api_error(401, "invalid_token", str(e)) from e
