"""議論役のツールと監査（S59）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.agent.gateway import InMemoryAudit
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.a2a import TokenIdentity


async def persist_audit(
    session_factory: async_sessionmaker[AsyncSession],
    identity: TokenIdentity,
    audit: InMemoryAudit,
    task_id: str,
) -> None:
    """門番の記録（許可・拒否）を audit_events に残す。どのトークンのどのタスクかを添える。"""
    if not audit.entries:
        return
    async with session_factory() as session, session.begin():
        repo = AuditRepository(session)
        for entry in audit.entries:
            await repo.record(
                actor=f"a2a:{identity.token_id}",
                action="a2a.tool",
                target=identity.team,
                decision=entry["decision"],
                detail={**entry, "a2a_task_id": task_id},
            )
