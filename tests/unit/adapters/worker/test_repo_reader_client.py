"""web → worker のリポジトリの読み取りの委譲（会話の調査）。クライアントと worker の入口を繋ぐ。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from speclino.adapters.worker.repo_reader_client import REPO_PATH, WorkerRepoReader
from speclino.api.deps import get_state
from speclino.api.routes import internal_github
from speclino.ports.repo_reader import RepoTree, RepoTreeEntry

SHA = "a" * 40


class Reader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def repository_id(self, repo: str) -> int | None:
        self.calls.append(("id", repo, ""))
        return 7

    async def branch_head(self, repo: str, branch: str) -> str | None:
        self.calls.append(("head", repo, branch))
        return SHA

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        self.calls.append(("tree", repo, commit_sha))
        return RepoTree([RepoTreeEntry("a.py", "100644", "b" * 40, 2)], truncated=True)

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        self.calls.append(("blob", repo, blob_sha))
        return b"\x00ok"


def _client(
    monkeypatch: pytest.MonkeyPatch, seen: list[httpx.Request]
) -> tuple[WorkerRepoReader, Reader]:
    reader = Reader()
    app = FastAPI()
    app.include_router(internal_github.router)
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(repo_reader=reader)
    inner = httpx.ASGITransport(app=app)

    class Spy(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return await inner.handle_async_request(request)

    client = WorkerRepoReader("https://worker.example/", transport=Spy())
    monkeypatch.setattr(client, "_id_token", lambda: "id-token")
    return client, reader


async def test_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    client, reader = _client(monkeypatch, seen)
    assert await client.repository_id("acme/app") == 7
    assert await client.branch_head("acme/app", "main") == SHA
    tree = await client.tree("acme/app", SHA)
    assert tree.entries == [RepoTreeEntry("a.py", "100644", "b" * 40, 2)] and tree.truncated
    assert await client.blob("acme/app", "b" * 40) == b"\x00ok"
    assert all(str(r.url) == f"https://worker.example{REPO_PATH}" for r in seen)
    assert seen[0].headers["authorization"] == "Bearer id-token"
    assert json.loads(seen[1].content) == {"op": "head", "repo": "acme/app", "ref": "main"}


async def test_worker_rejects_bad_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    client, reader = _client(monkeypatch, [])
    with pytest.raises(httpx.HTTPStatusError):
        await client.blob("acme/app", "../../x")
    with pytest.raises(httpx.HTTPStatusError):
        await client.tree("acme/../x", SHA)
    assert reader.calls == []
