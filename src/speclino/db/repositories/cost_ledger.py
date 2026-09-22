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
    ) -> CostLedgerEntry:
        entry = CostLedgerEntry(
            day=day,
            run_id=run_id,
            estimated_usd=estimated_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def total_for_day(self, day: date) -> Decimal:
        stmt = select(func.coalesce(func.sum(CostLedgerEntry.estimated_usd), 0)).where(
            CostLedgerEntry.day == day
        )
        result = await self._session.execute(stmt)
        total = result.scalar_one()
        return total if isinstance(total, Decimal) else Decimal(str(total))
