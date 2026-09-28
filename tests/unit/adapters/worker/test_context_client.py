"""web → worker の MCP context 委譲クライアント（S54）。"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from speclino.adapters.worker.context_client import CONTEXT_PATH, WorkerContextClient
from speclino.services.mcp_context import ContextUnavailable

PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _client(handler: httpx.MockTransport, monkeypatch: pytest.MonkeyPatch) -> WorkerContextClient:
    client = WorkerContextClient("https://worker.example/", transport=handler)
    monkeypatch.setattr(client, "_id_token", lambda: "id-token")
    return client


async def test_posts_the_project_and_ref_with_an_id_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"domain": {"variables": []}})

    got = await _client(httpx.MockTransport(handle), monkeypatch).get(PROJECT, "ws", "abc")

    assert got == {"domain": {"variables": []}}
    assert str(seen[0].url) == f"https://worker.example{CONTEXT_PATH}"
    assert seen[0].headers["authorization"] == "Bearer id-token"
    assert json.loads(seen[0].content) == {
        "project_id": str(PROJECT),
        "workspace": "ws",
        "ref": "abc",
    }


async def test_a_409_becomes_context_unavailable_with_the_workers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "context_unavailable", "message": "リポジトリ ID が登録時と違う"}}
        return httpx.Response(409, json=body)

    with pytest.raises(ContextUnavailable, match="リポジトリ ID が登録時と違う"):
        await _client(httpx.MockTransport(handle), monkeypatch).get(PROJECT, "ws", "abc")


async def test_other_errors_are_not_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    with pytest.raises(httpx.HTTPStatusError):
        await _client(httpx.MockTransport(handle), monkeypatch).get(PROJECT, "ws", "abc")
