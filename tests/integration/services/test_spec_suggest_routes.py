"""`POST /api/workspaces/{slug}/specs/suggest`（S67）の統合試験。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。LLM は台本（ネットワークなし）。
費用枠は本物の `ConsultBudget`（台帳に予約して精算する）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo
from tests.unit.spec.uml.samples import FULL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

MEMBER = {"X-Dev-User": "member1"}
STRANGER = {"X-Dev-User": "owner9"}
GOOD_GUARD = "[before.order.status == OrderStatus.PENDING]"
BAD = FULL.replace(GOOD_GUARD, "[注文が未払いのとき]")
BAD_LINE_NO = next(i for i, ln in enumerate(BAD.split("\n"), 1) if "注文が未払いのとき" in ln)
FIXED_LINE = BAD.split("\n")[BAD_LINE_NO - 1].replace("[注文が未払いのとき]", GOOD_GUARD)
RESPONSE = json.dumps(
    {
        "suggestions": [
            {
                "start_line": BAD_LINE_NO,
                "end_line": BAD_LINE_NO,
                "replacement": FIXED_LINE,
                "reason": "自然言語のガードを式にする",
            }
        ]
    },
    ensure_ascii=False,
)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    slug: str
    state: AppState
    llm: ScriptedJsonLlm


async def _make(engine: AsyncEngine, tmp_path: Path, limit: str | None) -> AsyncIterator[Ctx]:
    slug = f"e2e-sg-{uuid.uuid4().hex[:8]}"
    other = f"e2e-sgo-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}", {"owner1": "owner", "member1": "member"}, slug)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    guard = (
        None
        if limit is None
        else CostGuard(
            InMemoryLedger([]),
            Decimal(limit),
            per_run_limit_usd=Decimal("0"),
            pricing={"scripted": (Decimal("1"), Decimal("1"))},
        )
    )
    service = ReviewRunService(
        factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False), cost_guard=guard
    )
    llm = ScriptedJsonLlm(RESPONSE)
    state = AppState(settings, engine, factory, host, queue, blobs, service, spec_suggest_llm=llm)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield Ctx(c, slug, state, llm)


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    # 台帳は実 DB の cost_ledger（テスト同士で共有）。単価 1 USD / 1M トークンで十分な枠
    async for c in _make(engine, tmp_path, "1000000"):
        yield c


def _url(slug: str) -> str:
    return f"/api/workspaces/{slug}/specs/suggest"


async def test_member_gets_revalidated_suggestions(ctx: Ctx) -> None:
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": BAD}, headers=MEMBER)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["errors_before"] == 2
    [s] = body["suggestions"]
    assert s["after"]["errors_after"] == 0
    assert s["after"]["compiles"] is True
    assert BAD[: s["start"]] + s["replacement"] + BAD[s["end"] :] == FULL
    assert "compiled" not in res.text
    # モデルには本文と、サーバが作った診断が渡る（クライアントの診断は受け取らない）
    assert "E_GUARD_SYNTAX" in ctx.llm.prompts[0]


async def test_reservation_is_settled_in_the_ledger(ctx: Ctx) -> None:
    today = datetime.now(UTC).date()
    async with ctx.state.session_factory() as s:
        before = await CostLedgerRepository(s).total_for_day(today)
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": BAD}, headers=MEMBER)
    assert res.status_code == 200, res.text
    async with ctx.state.session_factory() as s:
        after = await CostLedgerRepository(s).total_for_day(today)
    # 予約（最悪の費用）は実費（1000 + 200 トークン × 1 USD/1M）に精算される
    assert after - before == Decimal("0.0012")


async def test_other_workspace_is_404(ctx: Ctx) -> None:
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": BAD}, headers=STRANGER)
    assert res.status_code == 404
    res = await ctx.client.post(_url("no-such-" + uuid.uuid4().hex[:6]), json={"markdown": BAD})
    assert res.status_code == 404
    assert ctx.llm.prompts == []


async def test_body_over_limit_is_413(ctx: Ctx) -> None:
    huge = "a" * (512 * 1024 + 1)
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": huge}, headers=MEMBER)
    assert res.status_code == 413


async def test_markdown_over_256kb_is_413(ctx: Ctx) -> None:
    big = "a" * (256 * 1024 + 1)
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": big}, headers=MEMBER)
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "spec_too_large"
    assert ctx.llm.prompts == []


async def test_bad_body_is_422(ctx: Ctx) -> None:
    res = await ctx.client.post(
        _url(ctx.slug), json={"markdown": "x", "diagnostics": []}, headers=MEMBER
    )
    assert res.status_code == 422


async def test_malformed_model_response_is_502(ctx: Ctx) -> None:
    ctx.llm.responses = ["not json"]
    res = await ctx.client.post(_url(ctx.slug), json={"markdown": BAD}, headers=MEMBER)
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "suggest_failed"


async def test_over_budget_is_429(engine: AsyncEngine, tmp_path: Path) -> None:
    async for c in _make(engine, tmp_path, "0"):
        res = await c.client.post(_url(c.slug), json={"markdown": BAD}, headers=MEMBER)
        assert res.status_code == 429
        assert res.json()["error"]["code"] == "cost_limit"
        assert c.llm.prompts == []


async def test_without_cost_guard_is_503(engine: AsyncEngine, tmp_path: Path) -> None:
    async for c in _make(engine, tmp_path, None):
        res = await c.client.post(_url(c.slug), json={"markdown": BAD}, headers=MEMBER)
        assert res.status_code == 503
        assert c.llm.prompts == []
