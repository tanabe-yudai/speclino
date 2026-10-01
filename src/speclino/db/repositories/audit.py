"""`audit_events` の repository（S14）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import AuditEvent
from speclino.domain.policy_view import AuditEntry


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

    async def list_for_team(
        self, team: str, actions: tuple[str, ...], limit: int
    ) -> list[AuditEntry]:
        """target が `<team>@<改訂番号>` に完全に一致し、action が actions のどれかの監査。

        新しい順。

        slug は `@` を含みうるので前方一致だけでは別のワークスペース（`<team>@x@1`）を拾う。
        残りが数字だけであることも確かめる。
        """
        rest = func.substr(AuditEvent.target, len(team) + 2)
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.target.startswith(f"{team}@", autoescape=True),
                rest.op("~")("^[0-9]+$"),
                AuditEvent.action.in_(actions),
            )
            .order_by(AuditEvent.ts.desc())
            .limit(limit)
        )
        return [
            AuditEntry(
                ts=e.ts,
                actor=e.actor,
                action=e.action,
                target=e.target,
                decision=e.decision,
                detail=e.detail,
            )
            for e in (await self._session.execute(stmt)).scalars()
        ]
