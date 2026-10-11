"""GitHubClient: `CodeHost` ポートの GitHub 実装（S19）。

各リクエストで installation token を使う。5xx と 429 は最大 3 回、0.5s×2^n で再試行する。
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import tarfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from speclino.adapters.github.app_auth import GitHubAppAuth
from speclino.adapters.github.user_lookup import parse_user_response, validate_login
from speclino.domain.run import GIT_REF_PATTERN
from speclino.ports.github_users import GitHubUserLookupError
from speclino.ports.repo_reader import RepoTree, RepoTreeEntry

_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 0.5
_RETRYABLE_CLIENT_STATUS = 429
_ANNOTATIONS_PER_REQUEST = 50
# 読むだけの API の URL に入れる値の形。リポジトリは `owner/name`、SHA は 40 桁の 16 進だけ
_REPO_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}"
_SHA_PATTERN = r"[0-9a-f]{40}"


def _checked(repo: str, sha: str) -> None:
    if not re.fullmatch(_REPO_PATTERN, repo) or ".." in repo or not re.fullmatch(_SHA_PATTERN, sha):
        raise ValueError("不正なリポジトリ名または SHA")


def _should_retry(status_code: int) -> bool:
    return status_code >= 500 or status_code == _RETRYABLE_CLIENT_STATUS


@dataclass
class GitHubClient:
    """CodeHost を実装する。`auth` と対象リポジトリのインストールで認証する。

    `installation_id` が 0（未指定）のときは、呼び出しごとにリポジトリから解決する。
    webhook 由来の run は payload の ID を渡せるが、手動開始・MCP・再実行では payload が
    無いため、この解決が要る。
    """

    auth: GitHubAppAuth
    installation_id: int
    http: httpx.AsyncClient
    base_url: str = "https://api.github.com"

    def __post_init__(self) -> None:
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def _installation_for(self, repo: str) -> int:
        if self.installation_id:
            return self.installation_id
        return await self.auth.installation_id_for_repo(repo, self.http, self.base_url)

    async def _headers(self, repo: str) -> dict[str, str]:
        installation_id = await self._installation_for(repo)
        token = await self.auth.installation_token(installation_id, self.http, self.base_url)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        repo: str,
        json_body: object | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        headers = await self._headers(repo)
        response: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES + 1):
            response = await self.http.request(
                method,
                url,
                headers=headers,
                json=json_body,
                follow_redirects=follow_redirects,
            )
            if not _should_retry(response.status_code):
                response.raise_for_status()
                return response
            if attempt < _MAX_RETRIES:
                await self._sleep(_RETRY_BASE_DELAY_SECONDS * (2**attempt))
        assert response is not None
        response.raise_for_status()
        return response

    async def create_check(
        self,
        repo: str,
        head_sha: str,
        name: str,
        details_url: str | None,
        external_id: str | None,
    ) -> int:
        body: dict[str, object] = {"name": name, "head_sha": head_sha}
        if details_url is not None:
            body["details_url"] = details_url
        if external_id is not None:
            body["external_id"] = external_id
        response = await self._request(
            "POST", f"{self.base_url}/repos/{repo}/check-runs", repo=repo, json_body=body
        )
        return int(response.json()["id"])

    async def update_check(
        self,
        repo: str,
        check_run_id: int,
        status: str,
        conclusion: str | None,
        title: str,
        summary: str,
        text: str,
        annotations: list[dict[str, object]],
    ) -> None:
        url = f"{self.base_url}/repos/{repo}/check-runs/{check_run_id}"
        chunks = [
            annotations[i : i + _ANNOTATIONS_PER_REQUEST]
            for i in range(0, len(annotations), _ANNOTATIONS_PER_REQUEST)
        ] or [[]]
        for chunk in chunks:
            body: dict[str, object] = {
                "status": status,
                "output": {
                    "title": title,
                    "summary": summary,
                    "text": text,
                    "annotations": chunk,
                },
            }
            if conclusion is not None:
                body["conclusion"] = conclusion
            await self._request("PATCH", url, repo=repo, json_body=body)

    async def post_comment(self, repo: str, issue_number: int, body: str) -> int:
        response = await self._request(
            "POST",
            f"{self.base_url}/repos/{repo}/issues/{issue_number}/comments",
            repo=repo,
            json_body={"body": body},
        )
        return int(response.json()["id"])

    async def upsert_comment(self, repo: str, issue_number: int, marker: str, body: str) -> int:
        """`marker`（HTML コメントの目印）を含む自分のコメントを探して更新する。

        見つからなければ新規投稿する。push のたびに判定コメントが積み上がるのを防ぐ。
        """
        listed = await self._request(
            "GET",
            f"{self.base_url}/repos/{repo}/issues/{issue_number}/comments?per_page=100",
            repo=repo,
        )
        for comment in listed.json():
            if marker in str(comment.get("body", "")):
                updated = await self._request(
                    "PATCH",
                    f"{self.base_url}/repos/{repo}/issues/comments/{comment['id']}",
                    repo=repo,
                    json_body={"body": body},
                )
                return int(updated.json()["id"])
        return await self.post_comment(repo, issue_number, body)

    async def repository_id(self, repo: str) -> int | None:
        """`GET /repos/{owner}/{repo}` の `id`。App がインストールされていない・無いなら None。"""
        try:
            response = await self._request("GET", f"{self.base_url}/repos/{repo}", repo=repo)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return int(response.json()["id"])

    async def branch_head(self, repo: str, branch: str) -> str | None:
        """`GET /repos/{owner}/{repo}/branches/{branch}` の `commit.sha`。無ければ None。"""
        if not re.fullmatch(GIT_REF_PATTERN, branch):
            return None
        try:
            response = await self._request(
                "GET", f"{self.base_url}/repos/{repo}/branches/{branch}", repo=repo
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        sha = response.json().get("commit", {}).get("sha")
        return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        """`GET /repos/{repo}/git/trees/{sha}?recursive=1` の blob（`RepoReader` の実装）。"""
        _checked(repo, commit_sha)
        response = await self._request(
            "GET", f"{self.base_url}/repos/{repo}/git/trees/{commit_sha}?recursive=1", repo=repo
        )
        data = response.json()
        entries = [
            RepoTreeEntry(
                path=str(e["path"]),
                mode=str(e["mode"]),
                sha=str(e["sha"]),
                size=e.get("size") if isinstance(e.get("size"), int) else None,
            )
            for e in data.get("tree", [])
            if e.get("type") == "blob" and isinstance(e.get("path"), str)
        ]
        return RepoTree(entries, bool(data.get("truncated")))

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        """`GET /repos/{repo}/git/blobs/{sha}` の中身（base64 を解く）。"""
        _checked(repo, blob_sha)
        response = await self._request(
            "GET", f"{self.base_url}/repos/{repo}/git/blobs/{blob_sha}", repo=repo
        )
        data = response.json()
        if data.get("encoding") != "base64":
            raise ValueError("blob の符号化が base64 ではない")
        return base64.b64decode(str(data.get("content", "")))

    async def fetch_snapshot(self, repo: str, sha: str, dest_dir: Path) -> Path:
        # ref は URL のパスに埋め込む。`..` 等で別リポジトリのパスに移れないよう、形を限る（S54）
        if not re.fullmatch(GIT_REF_PATTERN, sha):
            raise ValueError(f"不正な ref: {sha!r}")
        response = await self._request(
            "GET",
            f"{self.base_url}/repos/{repo}/tarball/{sha}",
            repo=repo,
            follow_redirects=True,
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
            names = tar.getnames()
            tar.extractall(dest_dir, filter="data")
        top_levels = {name.split("/", 1)[0] for name in names if name}
        if len(top_levels) != 1:
            raise ValueError(f"tarball の最上位ディレクトリが一意ではない: {sorted(top_levels)}")
        return dest_dir / next(iter(top_levels))

    async def user_id(self, login: str) -> int | None:
        """`GitHubUserLookup` の実装（S61）。`GET /users/{login}` を installation token で引く。

        認証なしの公開 API は Cloud Run の共有 IP でレート制限に当たるので、App の認証で引く。
        ユーザー情報はリポジトリに紐付かないので、インストールは App のどれでもよい。
        """
        validate_login(login)
        try:
            installation_id = self.installation_id or await self.auth.any_installation_id(
                self.http, self.base_url
            )
            token = await self.auth.installation_token(installation_id, self.http, self.base_url)
            response = await self.http.get(
                f"{self.base_url}/users/{login}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        except httpx.HTTPStatusError as e:
            raise GitHubUserLookupError(
                f"GitHub App の認証の応答が {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            raise GitHubUserLookupError(f"GitHub に問い合わせられない（{type(e).__name__}）") from e
        except LookupError as e:
            raise GitHubUserLookupError(str(e)) from e
        return parse_user_response(response)


__all__ = ["GitHubClient"]
