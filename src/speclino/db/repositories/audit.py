"""`audit_events` の repository（S14）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import AuditEvent


class AuditRepository:
    """`audit_events` テーブルへの追記を担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        actor: str,
        action: str,
        target: str,
        decision: str,
        detail: dict[str, Any],
        run_id: uuid.UUID | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor=actor,
            action=action,
            target=target,
            decision=decision,
            detail=detail,
            run_id=run_id,
        )
        self._session.add(event)
        await self._session.flush()
        return event
