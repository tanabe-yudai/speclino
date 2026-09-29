"""GitHub App のユーザー認可（web application flow）と、ユーザートークンでの確認（S55）。

公式: GitHub Docs「Generating a user access token for a GitHub App」（web application flow）
- 認可 URL に client_id・redirect_uri・state・PKCE（S256）を付ける。redirect_uri は App の
  Callback URL と完全一致で、トークン交換にも同じ値を送る
- トークン交換（`/login/oauth/access_token`）は `Accept: application/json` を付けないと
  URL エンコード形式で返る
- インストール済みかどうかは `GET /repos` の 200 では分からない（公開リポジトリは誰でも読める）。
  `GET /user/installations` と `/user/installations/{id}/repositories` に列挙されること、
  かつ `permissions.admin` で確かめる
- 使い終わったら `DELETE /applications/{client_id}/grant` で認可ごと取り消す（access token と
  refresh token がまとめて失効する。`/token` は access token 1 本だけで refresh token が残る）

トークンの値は、例外の文面・ログに出さない（例外は理由だけを持つ `GitHubOAuthError` に包み直す）。
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import urlencode

import httpx

from speclino.domain.workspace import AccessibleRepository

WEB_BASE = "https://github.com"
API_BASE = "https://api.github.com"
_API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_PER_PAGE = 100


class GitHubOAuthError(Exception):
    """GitHub での認可・確認の失敗。文面は利用者に見せてよい（トークンを含めない）。"""


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(*, client_id: str, redirect_uri: str, state: str, code_verifier: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{WEB_BASE}/login/oauth/authorize?{query}"


class GitHubUserOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        web_base: str = WEB_BASE,
        api_base: str = API_BASE,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport  # テストで差し替える
        self.web_base = web_base
        self.api_base = api_base

    async def exchange(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        """code をユーザーの access token に交換する。

        refresh token は使わない（`revoke` が認可ごと取り消すので、一緒に失効する）。
        """
        async with self._http() as http:
            response = await http.post(
                f"{self.web_base}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
            )
        body = _json_or_empty(response)
        token = body.get("access_token")
        if response.status_code != 200 or not isinstance(token, str) or not token:
            # エラーの種類（bad_verification_code / redirect_uri_mismatch 等）だけを出す
            raise GitHubOAuthError(
                f"GitHub での認可を確認できない（{body.get('error', 'unknown')}）"
            )
        return token

    async def find_repository(self, token: str, full_name: str) -> AccessibleRepository | None:
        """App がインストールされていて本人がアクセスできるリポジトリから full_name を探す。"""
        async with self._http(token) as http:
            async for installation in self._pages(http, "/user/installations", "installations"):
                installation_id = int(installation["id"])
                path = f"/user/installations/{installation_id}/repositories"
                async for repo in self._pages(http, path, "repositories"):
                    if str(repo.get("full_name", "")).lower() == full_name.lower():
                        return AccessibleRepository(
                            github_repo_id=int(repo["id"]),
                            full_name=str(repo["full_name"]),
                            default_branch=str(repo.get("default_branch") or "main"),
                            installation_id=installation_id,
                            admin=(repo.get("permissions") or {}).get("admin") is True,
                        )
        return None

    async def revoke(self, token: str) -> None:
        """このユーザーの SpecLino への認可を取り消す（access / refresh token がすべて失効する）。

        Basic 認証は client_id:client_secret。成功は 204 だけ（それ以外は失敗として扱う）。
        """
        async with self._http() as http:
            response = await http.request(
                "DELETE",
                f"{self.api_base}/applications/{self.client_id}/grant",
                auth=(self.client_id, self.client_secret),
                headers=_API_HEADERS,
                json={"access_token": token},
            )
        if response.status_code != 204:
            raise GitHubOAuthError(f"GitHub の認可を取り消せない（HTTP {response.status_code}）")

    def _http(self, token: str | None = None) -> httpx.AsyncClient:
        headers = dict(_API_HEADERS)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            base_url=self.api_base, headers=headers, timeout=30.0, transport=self.transport
        )

    async def _pages(self, http: httpx.AsyncClient, path: str, key: str) -> Any:
        page = 1
        while True:
            response = await http.get(path, params={"per_page": _PER_PAGE, "page": page})
            if response.status_code != 200:
                raise GitHubOAuthError(
                    f"GitHub の {path} を読めない（HTTP {response.status_code}）"
                )
            items = _json_or_empty(response).get(key) or []
            for item in items:
                yield item
            if len(items) < _PER_PAGE:
                return
            page += 1


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


__all__ = ["GitHubOAuthError", "GitHubUserOAuth", "authorize_url", "pkce_challenge"]
