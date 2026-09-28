"""`webhook_deliveries` の repository（S14）。GitHub webhook の重複排除に使う。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import WebhookDelivery


class WebhookDeliveryRepository:
    """`webhook_deliveries` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_if_new(self, delivery_id: str, event: str, action: str | None) -> bool:
        """`delivery_id` を `INSERT ... ON CONFLICT DO NOTHING` し、新規なら True を返す。

        GitHub webhook は少なくとも1回配信を保証しない前提で再配信されるため、
        delivery_id の一意性を DB 側の原子的な INSERT で保証する（アプリ側の
        SELECT→INSERT はレースする）。
        """
        stmt = (
            pg_insert(WebhookDelivery)
            .values(delivery_id=delivery_id, event=event, action=action)
            .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
        )
        result = await self._session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount > 0

    async def forget(self, delivery_id: str) -> None:
        """記録を消す。処理を commit したあとの enqueue に失敗したとき、再配信で処理し直せるように
        する（S53、`api/routes/webhooks.py`）。"""
        await self._session.execute(
            delete(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )
