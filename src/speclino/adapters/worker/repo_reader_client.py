"""web から worker の `POST /internal/github/repo` を呼ぶ `RepoReader`（知識の会話の調査）。

GitHub App の秘密鍵を持つのは worker だけなので、web はリポジトリの読み取りを OIDC で worker に
任せる（`github_user_client.py` と同じ方式）。web は、ワークスペースに登録されたリポジトリと、
木から引いた 40 桁の SHA だけを渡す。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Literal

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from speclino.ports.repo_reader import RepoTree, RepoTreeEntry

REPO_PATH = "/internal/github/repo"
RepoOp = Literal["id", "head", "tree", "blob"]


class WorkerRepoReader:
    def __init__(
        self,
        worker_url: str,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport  # テストで差し替える

    async def _call(self, op: RepoOp, repo: str, ref: str = "") -> dict[str, Any]:
        token = await asyncio.to_thread(self._id_token)
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as http:
            response = await http.post(
                f"{self.worker_url}{REPO_PATH}",
                json={"op": op, "repo": repo, "ref": ref},
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    async def repository_id(self, repo: str) -> int | None:
        value = (await self._call("id", repo)).get("id")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    async def branch_head(self, repo: str, branch: str) -> str | None:
        value = (await self._call("head", repo, branch)).get("sha")
        return value if isinstance(value, str) else None

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        data = await self._call("tree", repo, commit_sha)
        return RepoTree(
            [
                RepoTreeEntry(
                    path=str(e["path"]),
                    mode=str(e["mode"]),
                    sha=str(e["sha"]),
                    size=e.get("size") if isinstance(e.get("size"), int) else None,
                )
                for e in data.get("entries", [])
            ],
            bool(data.get("truncated")),
        )

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        data = await self._call("blob", repo, blob_sha)
        return base64.b64decode(str(data.get("content_b64", "")))

    def _id_token(self) -> str:
        token: str = google_id_token.fetch_id_token(  # type: ignore[no-untyped-call]
            GoogleAuthRequest(), self.worker_url
        )
        return token


__all__ = ["REPO_PATH", "WorkerRepoReader"]
