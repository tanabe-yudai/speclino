"""`GitHubUserLookup` の実装: 公開 API `GET /users/{login}` を認証なしで呼ぶ（S61）。

ユーザー ID は公開情報で、秘密は要らない。呼ぶのは承認者を保存するときだけなので、認証なしの
レート制限（IP あたり 60 回/時）で足りる。404 は「そのユーザーが無い」（None）、それ以外の失敗
（403・429 のレート制限、5xx、通信の失敗）は `GitHubUserLookupError`。
"""

from __future__ import annotations

import re

import httpx

from speclino.ports.github_users import GitHubUserLookupError

API_BASE = "https://api.github.com"
_API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")


class PublicGitHubUserLookup:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self.transport = transport  # テストで差し替える
        self.api_base = api_base

    async def user_id(self, login: str) -> int | None:
        # ログイン名は URL のパスに埋め込む。別のパスに移れないよう、形を限る
        if not _GITHUB_LOGIN.fullmatch(login):
            raise ValueError(f"GitHub のログイン名ではない: {login!r}")
        try:
            async with httpx.AsyncClient(
                base_url=self.api_base,
                headers=_API_HEADERS,
                timeout=10.0,
                transport=self.transport,
            ) as http:
                response = await http.get(f"/users/{login}")
        except httpx.HTTPError as e:
            raise GitHubUserLookupError(f"GitHub に問い合わせられない（{type(e).__name__}）") from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubUserLookupError(f"GitHub の応答が {response.status_code}")
        try:
            user_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as e:
            raise GitHubUserLookupError("GitHub の応答にユーザー ID が無い") from e
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise GitHubUserLookupError("GitHub の応答にユーザー ID が無い")
        return user_id


__all__ = ["PublicGitHubUserLookup"]
