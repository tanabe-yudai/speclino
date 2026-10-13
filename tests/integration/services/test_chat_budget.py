"""ChatBudget（S73）: 会話の費用の予約。実 PostgreSQL が必要。

全体の日次の枠（既存と共有）に加えて、ユーザーごと（1 日 50 発言・全体の 50%）と
ワークスペースごと（全体の 50%）の上限を、同じ advisory lock の中で数える。
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.knowledge_chat_budget import ChatBudget, ChatLimits

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]
MODEL = "e2e-chat-budget"


@pytest_asyncio.fixture
async def day(engine: AsyncEngine) -> AsyncIterator[date]:
    """他のテストの行と混ざらない、過去の日付。"""
    yield date(2000, 1, 1) + timedelta(days=random.randint(0, 5000))
    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM cost_ledger WHERE model = :m"), {"m": MODEL})


def _budget(engine: AsyncEngine, limit: str = "10", messages: int = 50) -> ChatBudget:
    guard = CostGuard(
        ledger=InMemoryLedger([]),
        daily_limit_usd=Decimal(limit),
        per_run_limit_usd=Decimal("0"),
        pricing={MODEL: (Decimal("1"), Decimal("1"))},
    )
    return ChatBudget(
        async_sessionmaker(engine, expire_on_commit=False),
        guard,
        ChatLimits(user_messages_per_day=messages),
    )


async def _reserve(b: ChatBudget, day: date, usd: str, ws: str = "w1", uid: str = "u1"):  # type: ignore[no-untyped-def]
    return await b.reserve(day, MODEL, Decimal(usd), workspace=ws, uid=uid, purpose="chat_message")


async def test_user_share_is_fifty_percent(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine)  # 全体 10 → 本人 5、ワークスペース 5
    assert (await _reserve(b, day, "4.5"))[0] is not None
    reservation, code = await _reserve(b, day, "0.6")
    assert reservation is None and code == "cost_limit"
    # 別の人は本人の枠では数えない（別のワークスペースなら通る）
    assert (await _reserve(b, day, "4.5", ws="w2", uid="u2"))[0] is not None


async def test_workspace_share_is_fifty_percent(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine)
    for uid in ("a", "b"):
        assert (await _reserve(b, day, "2", uid=uid))[0] is not None
    reservation, code = await _reserve(b, day, "1.5", uid="c")
    assert reservation is None and code == "cost_limit"
    assert (await _reserve(b, day, "1.5", ws="w2", uid="c"))[0] is not None


async def test_message_count_limit(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine, messages=3)
    for _ in range(3):
        assert (await _reserve(b, day, "0.01"))[0] is not None
    reservation, code = await _reserve(b, day, "0.01")
    assert reservation is None and code == "message_limit"
    # 要約は発言に数えない
    summary, _ = await b.reserve(
        day, MODEL, Decimal("0.01"), workspace="w1", uid="u1", purpose="chat_summary"
    )
    assert summary is not None


async def test_global_limit_is_shared(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine, limit="1")
    async with async_sessionmaker(engine)() as s, s.begin():
        await CostLedgerRepository(s).add(day, None, Decimal("0.9"), 0, 0, MODEL)
    reservation, code = await _reserve(b, day, "0.15")
    assert reservation is None and code == "cost_limit"


async def test_settle_attributes_actual_cost_to_user(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine)
    reservation, _ = await _reserve(b, day, "4.9")
    assert reservation is not None
    await b.settle(reservation, 100_000, 0)  # 実費 0.1
    async with async_sessionmaker(engine)() as s:
        used = await CostLedgerRepository(s).total_for_day(day, uid="u1")
    assert used == Decimal("0.1")
    assert (await _reserve(b, day, "4.8"))[0] is not None


async def test_parallel_reserves_respect_user_limit(engine: AsyncEngine, day: date) -> None:
    b = _budget(engine)
    results = await asyncio.gather(*(_reserve(b, day, "0.5") for _ in range(16)))
    assert sum(1 for r, _ in results if r is not None) == 10  # 本人の枠 5 ÷ 0.5
