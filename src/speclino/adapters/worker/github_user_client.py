"""web から worker の `POST /internal/github/user-id` を呼ぶ `GitHubUserLookup`（S61）。

承認者のログイン名 → ユーザー ID を、認証なしの公開 API で引くと Cloud Run の共有 IP で
レート制限（60 回/時）に当たる。GitHub App の秘密鍵を持つのは worker だけなので、web は
MCP の `context_get`（`context_client.py`）と同じく OIDC で worker に任せる。
"""

from __future__ import annotations

import asyncio

import httpx
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from speclino.adapters.github.user_lookup import validate_login
from speclino.ports.github_users import GitHubUserLookupError

USER_ID_PATH = "/internal/github/user-id"


class WorkerGitHubUserLookup:
    def __init__(
        self,
        worker_url: str,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport  # テストで差し替える

    async def user_id(self, login: str) -> int | None:
        validate_login(login)
        try:
            token = await asyncio.to_thread(self._id_token)
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as http:
                response = await http.post(
                    f"{self.worker_url}{USER_ID_PATH}",
                    json={"login": login},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except (httpx.HTTPError, GoogleAuthError) as e:
            raise GitHubUserLookupError(f"worker に問い合わせられない（{type(e).__name__}）") from e
        if response.status_code == 503:
            # worker は GitHub の失敗を ErrorResponse（{"error": {"code", "message"}}）で返す
            raise GitHubUserLookupError(_error_message(response))
        if response.status_code != 200:
            raise GitHubUserLookupError(f"worker の応答が {response.status_code}")
        try:
            user_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as e:
            raise GitHubUserLookupError("worker の応答にユーザー ID が無い") from e
        if user_id is None:
            return None
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise GitHubUserLookupError("worker の応答にユーザー ID が無い")
        return user_id

    def _id_token(self) -> str:
        token: str = google_id_token.fetch_id_token(  # type: ignore[no-untyped-call]
            GoogleAuthRequest(), self.worker_url
        )
        return token


def _error_message(response: httpx.Response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = None
    return str(message) if message else f"worker の応答が {response.status_code}"


__all__ = ["USER_ID_PATH", "WorkerGitHubUserLookup"]
