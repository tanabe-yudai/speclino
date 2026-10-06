"""`cost_ledger` の repository（S14）。費用ガードの日次累積枠に使う。"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import CostLedgerEntry


class CostLedgerRepository:
    """`cost_ledger` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        day: date,
        run_id: uuid.UUID | None,
        estimated_usd: Decimal,
        input_tokens: int,
        output_tokens: int,
        model: str,
        *,
        workspace: str | None = None,
        uid: str | None = None,
        purpose: str | None = None,
    ) -> CostLedgerEntry:
        entry = CostLedgerEntry(
            day=day,
            run_id=run_id,
            estimated_usd=estimated_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            workspace=workspace,
            uid=uid,
            purpose=purpose,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def total_for_day(
        self, day: date, *, workspace: str | None = None, uid: str | None = None
    ) -> Decimal:
        """その日の合計。workspace / uid を渡せばその行だけ（S73 の会話の上限）。"""
        stmt = select(func.coalesce(func.sum(CostLedgerEntry.estimated_usd), 0)).where(
            CostLedgerEntry.day == day
        )
        if workspace is not None:
            stmt = stmt.where(CostLedgerEntry.workspace == workspace)
        if uid is not None:
            stmt = stmt.where(CostLedgerEntry.uid == uid)
        result = await self._session.execute(stmt)
        total = result.scalar_one()
        return total if isinstance(total, Decimal) else Decimal(str(total))

    async def count_reservations(self, day: date, *, uid: str, purpose: str) -> int:
        """その日に uid が purpose で予約した回数（S73: 1 日の発言数）。"""
        stmt = select(func.count()).where(
            CostLedgerEntry.day == day,
            CostLedgerEntry.uid == uid,
            CostLedgerEntry.purpose == purpose,
        )
        return int((await self._session.execute(stmt)).scalar_one())
