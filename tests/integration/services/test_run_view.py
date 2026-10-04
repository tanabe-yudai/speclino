"""run_view: ワークスペースの run だけを返し、full=True で Finding の payload 全体を返す。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.services.run_view import run_view
from tests.integration.services.conftest import project_id_of, register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


async def _run_with_finding(engine: AsyncEngine, team: str) -> uuid.UUID:
    repo = f"e2e/rv-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {}, team=team)
    pid = await project_id_of(engine, repo)
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO review_runs (id, project_id, head_sha, trigger, exec_state, "
                "gate_status, budget, next_event_seq, created_at) VALUES (:id, :pid, 'abc1234', "
                "'test', 'completed', 'fail', '{}', 2, clock_timestamp())"
            ),
            {"id": run_id, "pid": pid},
        )
        await conn.execute(
            sql_text(
                "INSERT INTO findings (id, run_id, requirement_id, behavior_id, verdict, "
                "category, severity, payload, escalation_state) VALUES (gen_random_uuid(), :rid, "
                "'R-1', 'B-1', 'VIOLATED', 'c', 'high', CAST(:p AS jsonb), 'none')"
            ),
            {
                "rid": run_id,
                "p": '{"requirement_id": "R-1", "verdict": "VIOLATED", "counterexample": {"x": 1}}',
            },
        )
    return run_id


async def test_full_returns_whole_payload_and_default_is_projection(engine: AsyncEngine) -> None:
    run_id = await _run_with_finding(engine, "e2e-rv-a")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    brief = await run_view(sf, run_id, "e2e-rv-a")
    full = await run_view(sf, run_id, "e2e-rv-a", full=True)
    assert brief is not None and full is not None
    assert brief["findings"] == [{"requirement_id": "R-1", "verdict": "VIOLATED"}]
    assert full["findings"][0]["counterexample"] == {"x": 1}


async def test_other_workspace_or_missing_run_is_none(engine: AsyncEngine) -> None:
    run_id = await _run_with_finding(engine, "e2e-rv-a")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    assert await run_view(sf, run_id, "e2e-rv-b") is None
    assert await run_view(sf, uuid.uuid4(), "e2e-rv-a") is None
