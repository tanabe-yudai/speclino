"""services 統合テスト共通の fixture（Postgres エンジンと後片付け）。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from speclino.db.models import Base

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert _DATABASE_URL is not None
    eng = create_async_engine(_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        # 依存順に消す（projects ← review_runs ← findings / run_events / audit_events / approvals）
        pid = "SELECT id FROM projects WHERE repo_full_name LIKE 'e2e/%'"
        rid = f"SELECT id FROM review_runs WHERE project_id IN ({pid})"
        for table, col, sub in (
            ("findings", "run_id", rid),
            ("run_events", "run_id", rid),
            ("evidence", "run_id", rid),
            ("tool_executions", "run_id", rid),
            ("questions", "run_id", rid),
            ("cost_ledger", "run_id", rid),
            ("audit_events", "run_id", rid),
            ("review_runs", "project_id", pid),
            ("approvals", "project_id", pid),
            ("projects", "id", pid),
        ):
            await conn.execute(sql_text(f"DELETE FROM {table} WHERE {col} IN ({sub})"))
    await eng.dispose()
