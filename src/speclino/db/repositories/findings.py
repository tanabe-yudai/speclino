"""`findings` の repository（S14）。run ごとに Finding 全件を入れ替える。"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import FindingRecord
from speclino.domain.finding import Finding


class FindingRepository:
    """`findings` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_run(self, run_id: uuid.UUID, findings: list[Finding]) -> None:
        """run の Finding 全件を削除してから入れ直す（再評価・replay で冪等にするため）。"""
        await self._session.execute(delete(FindingRecord).where(FindingRecord.run_id == run_id))
        for finding in findings:
            self._session.add(
                FindingRecord(
                    run_id=run_id,
                    requirement_id=finding.requirement_id,
                    behavior_id=finding.behavior_id,
                    verdict=finding.verdict,
                    category=finding.category,
                    severity=finding.severity,
                    payload=finding.model_dump(mode="json"),
                    escalation_state="none",
                )
            )
        await self._session.flush()

    async def list_for_run(self, run_id: uuid.UUID) -> list[FindingRecord]:
        stmt = (
            select(FindingRecord)
            .where(FindingRecord.run_id == run_id)
            .order_by(FindingRecord.created_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
