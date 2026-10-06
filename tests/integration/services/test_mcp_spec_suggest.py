"""S67: MCP の `spec_suggest`。ワークスペースの MCP トークンだけが呼べ、sync トークンは拒否する。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。LLM は台本（ネットワークなし）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services import sync_tokens
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo
from tests.integration.services.test_mcp_flow import _issue as issue_mcp_token
from tests.integration.services.test_spec_suggest_routes import (
    TEST_MODEL,
    clear_scripted_ledger,
    scripted_llm,
)
from tests.unit.services.suggest_samples import BAD, FULL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


@asynccontextmanager
async def _client(
    engine: AsyncEngine, tmp: Path, token: str, llm: ScriptedJsonLlm, limit: str = "1000000"
) -> AsyncIterator[Client]:
    f = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(tmp / "blobs")
    settings = Settings(env="local", role="all", code_host_kind="local")
    guard = CostGuard(
        InMemoryLedger([]),
        Decimal(limit),
        per_run_limit_usd=Decimal("0"),
        pricing={TEST_MODEL: (Decimal("1"), Decimal("1"))},
    )
    service = ReviewRunService(
        f, host, queue, blobs, ReviewRunConfig(agent_enabled=False), cost_guard=guard
    )
    app = create_app(
        settings, AppState(settings, engine, f, host, queue, blobs, service, spec_suggest_llm=llm)
    )
    try:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
            Client(streamable_http_client("http://localhost/mcp", http_client=http)) as client,
        ):
            yield client
    finally:
        await clear_scripted_ledger(engine)


def _text(result: Any) -> str:
    return str(getattr(result.content[0], "text", ""))


async def _workspace(engine: AsyncEngine) -> tuple[str, str, str]:
    slug = f"e2e-ms-{uuid.uuid4().hex[:8]}"
    other = f"e2e-mso-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}", {"owner1": "owner"}, slug)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    return slug, f"e2e/{slug}", f"e2e/{other}"


async def test_mcp_token_gets_suggestions(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, repo, other_repo = await _workspace(engine)
    token = await issue_mcp_token(engine, slug)
    llm = scripted_llm()
    async with _client(engine, tmp_path, token, llm) as c:
        res = await c.call_tool("spec_suggest", {"markdown": BAD, "repo_full_name": repo})
        assert not res.is_error, _text(res)
        body = json.loads(_text(res))
        hidden = await c.call_tool("spec_suggest", {"markdown": BAD, "repo_full_name": other_repo})
    [s] = body["suggestions"]
    assert BAD[: s["start"]] + s["replacement"] + BAD[s["end"] :] == FULL
    assert s["after"]["compiles"] is True
    assert "compiled" not in _text(res)
    assert hidden.is_error and "登録されていない" in _text(hidden)
    assert len(llm.prompts) == 1


async def test_sync_token_cannot_call_spec_suggest(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, repo, _ = await _workspace(engine)
    async with async_sessionmaker(engine)() as s, s.begin():
        issued = await sync_tokens.issue(
            s,
            slug=slug,
            project_id=await project_id_of(engine, repo),
            name="t",
            actor_uid="owner1",
        )
    llm = scripted_llm()
    async with _client(engine, tmp_path, issued.token, llm) as c:
        res = await c.call_tool("spec_suggest", {"markdown": BAD, "repo_full_name": repo})
    assert res.is_error and "読み取り専用" in _text(res)
    assert llm.prompts == []


async def test_over_budget_is_an_error_result(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, _, _ = await _workspace(engine)
    token = await issue_mcp_token(engine, slug)
    llm = scripted_llm()
    async with _client(engine, tmp_path, token, llm, limit="0") as c:
        res = await c.call_tool("spec_suggest", {"markdown": BAD})
    assert res.is_error and "費用" in _text(res)
    assert llm.prompts == []
