"""MCP サーバー（S33）: 語彙の読出し、Bearer 認証、Streamable HTTP でのツール一覧。"""

from __future__ import annotations

from pathlib import Path

import httpx
import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from speclino.api.app import create_app
from speclino.mcp_server.server import _read_context, allowed_hosts, build_mcp
from speclino.settings import Settings

REPO = Path(__file__).resolve().parents[2]
TOKEN = "test-mcp-token"


def test_read_context_lists_vocabulary_policies_and_rules() -> None:
    ctx = _read_context(REPO)
    assert "has_findings" in ctx["domain"]["variables"]
    assert "gate_decide" in ctx["bindings"]["behaviors"]
    assert {p["id"] for p in ctx["policies"]} >= {"MAKE-001", "SPEC-FMT-001", "CI-001"}
    by_id = {r["id"]: r for r in ctx["rules"]}
    assert by_id["R-TOOL-01"]["approved"] and by_id["R-TOOL-01"]["spec"] == "specs/SPEC-TOOL-001.md"
    assert by_id["R-GATE-01"]["behavior_id"] == "gate_decide"


def test_allowed_hosts_include_public_host_and_localhost() -> None:
    hosts = allowed_hosts("https://speclino-web-abc.a.run.app")
    assert "speclino-web-abc.a.run.app" in hosts and "localhost:*" in hosts


@pytest.mark.asyncio
async def test_tools_are_exposed_in_process() -> None:
    async with Client(build_mcp(lambda: None)) as client:  # type: ignore[arg-type, return-value]
        tools = await client.list_tools()
    assert {t.name for t in tools.tools} == {
        "context_get",
        "verification_start",
        "verification_get",
    }


@pytest.mark.asyncio
async def test_http_mount_requires_bearer_and_serves_tools() -> None:
    app = create_app(Settings(env="local", role="all", code_host_kind="local", mcp_token=TOKEN))
    async with app.router.lifespan_context(app):
        # 認証なし → 401（API-ERR-001 の形式）
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as plain:
            denied = await plain.post("/mcp", json={})
        assert denied.status_code == 401 and denied.json()["error"]["code"] == "unauthenticated"
        # Bearer つき → MCP クライアントからツール一覧が取れる
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with Client(transport) as client:
                tools = await client.list_tools()
    assert "verification_start" in {t.name for t in tools.tools}
