"""補足の保存と a2a_tasks 表（S59）。`SPECLINO_DATABASE_URL` が未設定なら skip。"""

from __future__ import annotations

import os
import uuid

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks import DatabaseTaskStore
from a2a.types import Task, TaskState, TaskStatus
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories import AgentSupplementRepository, ReviewRunRepository
from tests.integration.services.conftest import E2E_TEAM, project_id_of, register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


async def _make_run(engine: AsyncEngine) -> uuid.UUID:
    repo = f"e2e/supp-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo)
    project_id = await project_id_of(engine, repo)
    async with async_sessionmaker(engine)() as s, s.begin():
        run = await ReviewRunRepository(s).create(
            project_id=project_id, pr_number=None, head_sha="a" * 40, base_sha=None, trigger="a2a"
        )
        return run.id


async def test_add_and_list_supplements_in_order(engine: AsyncEngine) -> None:
    run_id = await _make_run(engine)
    async with async_sessionmaker(engine)() as s, s.begin():
        repo = AgentSupplementRepository(s)
        await repo.add(
            team=E2E_TEAM,
            run_id=run_id,
            requirement_id="R-1",
            question="q1",
            content="a1",
            token_id=None,
            a2a_task_id="task-1",
        )
    async with async_sessionmaker(engine)() as s, s.begin():
        await AgentSupplementRepository(s).add(
            team=E2E_TEAM,
            run_id=run_id,
            requirement_id=None,
            question="q2",
            content="a2",
            token_id=None,
            a2a_task_id="task-1",
        )
    async with async_sessionmaker(engine)() as s:
        rows = await AgentSupplementRepository(s).list_for_run(run_id)
    assert [(r.requirement_id, r.content) for r in rows] == [("R-1", "a1"), (None, "a2")]


async def test_a2a_tasks_table_matches_sdk_model(engine: AsyncEngine) -> None:
    """SDK の DatabaseTaskStore が create_table=False でこの表を読み書きできる。"""
    store = DatabaseTaskStore(
        engine, create_table=False, table_name="a2a_tasks", owner_resolver=lambda ctx: "e2e-a2a"
    )
    task = Task(
        id=str(uuid.uuid4()),
        context_id=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    ctx = ServerCallContext()
    await store.save(task, ctx)
    got = await store.get(task.id, ctx)
    assert got is not None and got.status.state == TaskState.TASK_STATE_WORKING
