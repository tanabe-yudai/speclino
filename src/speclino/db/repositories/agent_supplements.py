"""`agent_supplements` テーブル: A2A の議論で得た補足（S59）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import AgentSupplementRecord


class AgentSupplementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        team: str,
        run_id: uuid.UUID,
        requirement_id: str | None,
        question: str,
        content: str,
        token_id: uuid.UUID | None,
        a2a_task_id: str,
    ) -> AgentSupplementRecord:
        row = AgentSupplementRecord(
            team=team,
            run_id=run_id,
            requirement_id=requirement_id,
            question=question,
            content=content,
            token_id=token_id,
            a2a_task_id=a2a_task_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_run(self, run_id: uuid.UUID) -> list[AgentSupplementRecord]:
        stmt = (
            select(AgentSupplementRecord)
            .where(AgentSupplementRecord.run_id == run_id)
            .order_by(AgentSupplementRecord.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())
