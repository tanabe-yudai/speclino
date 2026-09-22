"""GitHub App 認証（S19）。App JWT の発行と installation token のキャッシュ。

JWT: RS256、`{"iat": now-60, "exp": now+540, "iss": app_id}`（exp は GitHub の上限である 10 分）。
installation token: `POST /app/installations/{id}/access_tokens`（1 時間有効）を
`expires_at` の 5 分前まで使い回す。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import jwt

_JWT_BACKDATE_SECONDS = 60
_JWT_TTL_SECONDS = 540  # 9分（GitHub の上限 10 分未満に余裕を持たせる）
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class _CachedToken:
    token: str
    expires_at: datetime


@dataclass
class GitHubAppAuth:
    app_id: int
    private_key_pem: str
    _cache: dict[int, _CachedToken] = field(default_factory=dict, init=False, repr=False)

    def app_jwt(self, now: int | None = None) -> str:
        """App 認証用 JWT（RS256）を発行する。"""
        base = now if now is not None else int(time.time())
        payload = {
            "iat": base - _JWT_BACKDATE_SECONDS,
            "exp": base + _JWT_TTL_SECONDS,
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self.private_key_pem, algorithm="RS256")

    async def installation_token(
        self, installation_id: int, http: httpx.AsyncClient, base_url: str
    ) -> str:
        """installation token を返す。有効なキャッシュがあれば HTTP を呼ばない。"""
        cached = self._cache.get(installation_id)
        now = datetime.now(UTC)
        if cached is not None and now < cached.expires_at - _TOKEN_REFRESH_MARGIN:
            return cached.token

        response = await http.post(
            f"{base_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        data = response.json()
        token = str(data["token"])
        expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
        self._cache[installation_id] = _CachedToken(token=token, expires_at=expires_at)
        return token


__all__ = ["GitHubAppAuth"]
