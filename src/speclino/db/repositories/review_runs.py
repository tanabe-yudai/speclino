"""`review_runs` の repository（S14）。

冪等性の要: `try_acquire` は `UPDATE ... WHERE exec_state = 'queued'` の rowcount で
実行権を原子的に取得する。webhook の再配信で同じ run を二重実行しないための唯一の関門。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import ReviewRun
from speclino.domain.run import ExecState, GateStatus

_OPEN_STATES: tuple[ExecState, ...] = ("queued", "running", "awaiting_answer")


class ReviewRunRepository:
    """`review_runs` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        pr_number: int | None,
        head_sha: str,
        base_sha: str | None,
        trigger: str,
        budget: dict[str, Any] | None = None,
        parent_run_id: uuid.UUID | None = None,
    ) -> ReviewRun:
        run = ReviewRun(
            project_id=project_id,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            trigger=trigger,
            budget=budget if budget is not None else {},
            parent_run_id=parent_run_id,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> ReviewRun | None:
        return await self._session.get(ReviewRun, run_id)

    async def try_acquire(self, run_id: uuid.UUID) -> bool:
        """`exec_state='queued'` の run だけを `running` にし、実行権を原子的に取得する。

        rowcount が 0 なら「他プロセスが既に取得済み／queued でない」ため、
        呼び出し側は webhook の再配信を 200 で無視してよい。
        """
        stmt = (
            update(ReviewRun)
            .where(ReviewRun.id == run_id, ReviewRun.exec_state == "queued")
            .values(exec_state="running", started_at=func.now())
        )
        result = await self._session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount > 0

    async def supersede_open_runs(
        self, project_id: uuid.UUID, pr_number: int | None, except_run_id: uuid.UUID
    ) -> int:
        """同一 PR の未完了 run（queued/running/awaiting_answer）を superseded にする。"""
        stmt = (
            update(ReviewRun)
            .where(
                ReviewRun.project_id == project_id,
                ReviewRun.pr_number == pr_number,
                ReviewRun.id != except_run_id,
                ReviewRun.exec_state.in_(_OPEN_STATES),
            )
            .values(exec_state="superseded")
        )
        result = await self._session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount

    async def count_since(self, trigger_prefix: str, since: datetime) -> int:
        """`since` 以降に作られ trigger が prefix で始まる run の数（デモ再実行の回数制限）。"""
        stmt = (
            select(func.count())
            .select_from(ReviewRun)
            .where(ReviewRun.trigger.like(f"{trigger_prefix}%"), ReviewRun.created_at >= since)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def latest_run_for_pr(
        self, project_id: uuid.UUID, pr_number: int | None
    ) -> ReviewRun | None:
        stmt = (
            select(ReviewRun)
            .where(ReviewRun.project_id == project_id, ReviewRun.pr_number == pr_number)
            .order_by(ReviewRun.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_result(
        self,
        run_id: uuid.UUID,
        exec_state: ExecState,
        gate_status: GateStatus | None,
        manifest: dict[str, Any] | None,
        artifacts_uri: str | None,
    ) -> None:
        stmt = (
            update(ReviewRun)
            .where(ReviewRun.id == run_id)
            .values(
                exec_state=exec_state,
                gate_status=gate_status,
                manifest=manifest,
                artifacts_uri=artifacts_uri,
                finished_at=func.now(),
            )
        )
        await self._session.execute(stmt)

    async def set_check_run_id(self, run_id: uuid.UUID, check_run_id: int) -> None:
        stmt = update(ReviewRun).where(ReviewRun.id == run_id).values(check_run_id=check_run_id)
        await self._session.execute(stmt)

    async def latest_completed(self, project_id: uuid.UUID) -> ReviewRun | None:
        """最新の完了した run（S45。awaiting_answer は判定と成果物が出そろっている）。"""
        stmt = (
            select(ReviewRun)
            .where(
                ReviewRun.project_id == project_id,
                ReviewRun.exec_state.in_(("completed", "awaiting_answer")),
            )
            .order_by(ReviewRun.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_project(self, project_id: uuid.UUID, limit: int) -> list[ReviewRun]:
        stmt = (
            select(ReviewRun)
            .where(ReviewRun.project_id == project_id)
            .order_by(ReviewRun.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())
