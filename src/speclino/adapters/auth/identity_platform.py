"""Identity Platform（Firebase Auth）の ID トークン検証（S13）。

Google の公開鍵で署名を検証したうえで、`aud` = プロジェクト ID と
`iss` = `https://securetoken.google.com/<project>` を確認する。

`google-auth` の `verify_firebase_token` は署名と `aud` は見るが **`iss` は検証しない**ため、
発行者の確認はこのモジュールで明示的に行う（別の Google 発行トークンを取り違えないため）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from speclino.domain.workspace import AuthUser
from speclino.settings import Settings

ISSUER_PREFIX = "https://securetoken.google.com/"


def _check_claims(claims: dict[str, Any], project_id: str) -> AuthUser:
    """検証済みトークンのクレームから利用者を取り出す。識別子は `sub`（uid）だけ。

    メールは識別子にしない: Identity Platform は確認前のメールでも登録でき、メールで権限を
    与えると、そのメールで先にサインアップした第三者が権限を得る（S53）。
    """
    issuer = claims.get("iss")
    if issuer != f"{ISSUER_PREFIX}{project_id}":
        raise ValueError(f"発行者が不正: {issuer!r}")
    if claims.get("aud") != project_id:
        raise ValueError("aud がプロジェクト ID と一致しない")
    uid = claims.get("sub")
    if not uid:
        raise ValueError("利用者を識別できるクレーム（sub）が無い")
    email = claims.get("email")
    return AuthUser(
        uid=str(uid),
        email=str(email) if email else None,
        email_verified=claims.get("email_verified") is True,
    )


async def verify_bearer(token: str, settings: Settings) -> AuthUser:
    project_id = settings.firebase_project_id
    if not project_id:
        raise ValueError("firebase_project_id が未設定")
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as e:  # pragma: no cover
        raise ValueError("google-auth が無い") from e

    def _verify() -> dict[str, Any]:
        claims = id_token.verify_firebase_token(  # type: ignore[no-untyped-call]
            token, google_requests.Request(), audience=project_id
        )
        return dict(claims)

    try:
        claims = await asyncio.to_thread(_verify)
    except Exception as e:  # noqa: BLE001 - 署名・期限の検証失敗はすべて 401 にする
        raise ValueError(f"ID トークンの検証に失敗: {type(e).__name__}") from e
    return _check_claims(claims, project_id)


__all__ = ["ISSUER_PREFIX", "verify_bearer"]
