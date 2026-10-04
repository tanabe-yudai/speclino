"""ConsultBudget: 並行の予約が日次の枠を超えない（S59）。"""

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

from speclino.agent.consultant.tools import ConsultBudget
from speclino.services.cost_guard import CostGuard, InMemoryLedger

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]
MODEL = "e2e-consult"


@pytest_asyncio.fixture
async def day(engine: AsyncEngine) -> AsyncIterator[date]:
    """他のテストの行と混ざらない、過去の日付。"""
    yield date(2000, 1, 1) + timedelta(days=random.randint(0, 5000))
    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM cost_ledger WHERE model = :m"), {"m": MODEL})


def _budget(engine: AsyncEngine, limit: str = "1", per_run: str = "0") -> ConsultBudget:
    guard = CostGuard(
        ledger=InMemoryLedger([]),
        daily_limit_usd=Decimal(limit),
        per_run_limit_usd=Decimal(per_run),
    )
    return ConsultBudget(async_sessionmaker(engine, expire_on_commit=False), guard)


async def test_parallel_reserves_never_exceed_limit(engine: AsyncEngine, day: date) -> None:
    budget = _budget(engine)
    results = await asyncio.gather(*(budget.reserve(day, MODEL, Decimal("0.4")) for _ in range(5)))
    assert sum(1 for r, _ in results if r is not None) == 2


async def test_reserve_leaves_headroom_for_one_pipeline_run(engine: AsyncEngine, day: date) -> None:
    """パイプライン（can_start が per_run_limit_usd の余裕で判定する）の 1 回分を残す。"""
    budget = _budget(engine, limit="1", per_run="0.5")
    reservation, reason = await budget.reserve(day, MODEL, Decimal("0.6"))
    assert reservation is None and "パイプライン" in reason
    assert (await budget.reserve(day, MODEL, Decimal("0.5")))[0] is not None


async def test_settle_returns_unused_reservation(engine: AsyncEngine, day: date) -> None:
    budget = _budget(engine)
    first, _ = await budget.reserve(day, MODEL, Decimal("0.6"))
    assert first is not None
    assert (await budget.reserve(day, MODEL, Decimal("0.6")))[0] is None
    # 実費はほぼ 0（scripted 以外の既定単価でも微小）→ 予約が戻って次が通る
    await budget.settle(first, 10, 10)
    assert (await budget.reserve(day, MODEL, Decimal("0.6")))[0] is not None


async def test_settle_with_zero_usage_releases_everything(engine: AsyncEngine, day: date) -> None:
    budget = _budget(engine)
    reservation, _ = await budget.reserve(day, MODEL, Decimal("0.9"))
    assert reservation is not None
    await budget.settle(reservation, 0, 0)
    async with engine.connect() as conn:
        total = (
            await conn.execute(
                sql_text("SELECT COALESCE(SUM(estimated_usd), 0) FROM cost_ledger WHERE day = :d"),
                {"d": day},
            )
        ).scalar_one()
    assert Decimal(total) == 0
