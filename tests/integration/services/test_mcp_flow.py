"""MCP（S33）: エディタのエージェントが context_get → verification_start → verification_get を
Streamable HTTP + Bearer で実行できる（デモ 6 の前段: 仕様を書いてその場で検証する）。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.test_review_run_service import make_demo_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]
TOKEN = "mcp-secret"


def _text(result) -> dict:  # type: ignore[no-untyped-def]
    """ツール結果の最初のテキスト内容を JSON として読む。"""
    block = result.content[0]
    return json.loads(block.text)


async def test_context_start_get_over_mcp(engine: AsyncEngine, tmp_path: Path) -> None:
    repo = f"e2e/mcp-{uuid.uuid4().hex[:8]}"
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", code_host_kind="local", mcp_token=TOKEN)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http,
    ):
        transport = streamable_http_client("http://localhost/mcp", http_client=http)
        async with Client(transport) as client:
            ctx = _text(
                await client.call_tool("context_get", {"repo_full_name": repo, "ref": base})
            )
            assert "order.status" in ctx["domain"]["variables"]
            assert {r["id"] for r in ctx["rules"]} >= {"R-CANCEL-04"}
            started = _text(
                await client.call_tool(
                    "verification_start",
                    {"repo_full_name": repo, "head_sha": head, "base_sha": base},
                )
            )
            got = _text(await client.call_tool("verification_get", {"run_id": started["run_id"]}))
    assert got["exec_state"] == "completed" and got["gate_status"] == "fail", got
    assert got["semantic_diff"]["counts"]["new_violation"] == 1
    violated = [f for f in got["findings"] if f["verdict"] == "VIOLATED"]
    assert [f["requirement_id"] for f in violated] == ["R-CANCEL-04"]
    assert "next_steps" in violated[0] or violated[0]["category"] == "missing_effect"
