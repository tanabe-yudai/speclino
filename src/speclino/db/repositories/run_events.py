"""`run_events` の repository（S14）。

**seq の採番方式**: `review_runs.next_event_seq` を単一の `UPDATE ... RETURNING` で
インクリメントして採番する（`SELECT coalesce(max(seq),0)+1 ... FOR UPDATE` 方式は採らない）。

理由:
- `UPDATE` は対象行を自動的にロックするため、`SELECT ... FOR UPDATE` と同等の直列化が
  1 回のラウンドトリップで得られる（asyncpg 経由でも往復が少なく単純）。
- `run_events` テーブルを `run_id` でスキャン/集計する必要がなく、`review_runs` の
  1 行に対する更新だけで完結する（`run_events` 側に seq 用のインデックスが必須にならない）。
- 採番と `run_events` への INSERT を同一トランザクション内で行うため、採番だけ成功して
  INSERT が失敗するケースでも欠番は生じるが重複は生じない（欠番は許容、重複は不可という
  要件に合致）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import ReviewRun, RunEvent


class RunEventRepository:
    """`run_events` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, run_id: uuid.UUID, type: str, payload: dict[str, Any]) -> int:
        """`review_runs.next_event_seq` を原子的に進め、採番した seq でイベントを追加する。"""
        stmt = (
            update(ReviewRun)
            .where(ReviewRun.id == run_id)
            .values(next_event_seq=ReviewRun.next_event_seq + 1)
            .returning(ReviewRun.next_event_seq)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            raise ValueError(f"review run not found: {run_id}")
        seq = int(row[0]) - 1
        self._session.add(RunEvent(run_id=run_id, seq=seq, type=type, payload=payload))
        await self._session.flush()
        return seq

    async def list_for_run(self, run_id: uuid.UUID) -> list[RunEvent]:
        stmt = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
