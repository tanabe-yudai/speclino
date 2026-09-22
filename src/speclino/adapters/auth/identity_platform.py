"""Identity Platform（Firebase Auth）の ID トークン検証（S13）。

Google の公開鍵で署名を検証し、aud=プロジェクト ID、iss=https://securetoken.google.com/<project>
を確認する。google-auth の `verify_firebase_token` を使う。
"""

from __future__ import annotations

import asyncio

from speclino.settings import Settings


async def verify_bearer(token: str, settings: Settings) -> str:
    if not settings.firebase_project_id:
        raise ValueError("firebase_project_id が未設定")
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as e:  # pragma: no cover
        raise ValueError("google-auth が無い") from e

    def _verify() -> dict[str, object]:
        claims = id_token.verify_firebase_token(  # type: ignore[no-untyped-call]
            token, google_requests.Request(), audience=settings.firebase_project_id
        )
        return dict(claims)

    try:
        claims = await asyncio.to_thread(_verify)
    except Exception as e:  # noqa: BLE001 - 検証失敗はすべて 401 にする
        raise ValueError(f"ID トークンの検証に失敗: {type(e).__name__}") from e
    email = claims.get("email")
    uid = claims.get("user_id") or claims.get("sub")
    return str(email or uid)
