"""`bundles` の repository（S62）。bundle は不変。採番の排他は呼び出し側のロック。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import BundleRecord
from speclino.domain.knowledge import Bundle, BundleMember


def _to_domain(row: BundleRecord) -> Bundle:
    return Bundle(
        workspace=row.workspace,
        generation=row.generation,
        policy_revision=row.policy_revision,
        policy_hash=row.policy_hash,
        members=[BundleMember.model_validate(m) for m in row.members],
        scope_hash=row.scope_hash,
        key_id=row.key_id,
        signature=row.signature,
        created_at=row.created_at,
    )


class BundleRepository:
    """`bundles` テーブルの読み書き。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_generation(self, workspace: str) -> int:
        stmt = select(func.coalesce(func.max(BundleRecord.generation), 0)).where(
            BundleRecord.workspace == workspace
        )
        return int((await self._session.execute(stmt)).scalar_one()) + 1

    async def add(self, bundle: Bundle) -> None:
        self._session.add(
            BundleRecord(
                workspace=bundle.workspace,
                generation=bundle.generation,
                policy_revision=bundle.policy_revision,
                policy_hash=bundle.policy_hash,
                members=[m.model_dump(mode="json") for m in bundle.members],
                scope_hash=bundle.scope_hash,
                key_id=bundle.key_id,
                signature=bundle.signature,
                created_at=bundle.created_at,
            )
        )
        await self._session.flush()

    async def workspaces(self) -> list[str]:
        """bundle を持つワークスペース（名前順）。"""
        stmt = select(BundleRecord.workspace).distinct().order_by(BundleRecord.workspace)
        return list((await self._session.execute(stmt)).scalars())

    async def latest(self, workspace: str) -> Bundle | None:
        stmt = (
            select(BundleRecord)
            .where(BundleRecord.workspace == workspace)
            .order_by(BundleRecord.generation.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def at(self, workspace: str, generation: int) -> Bundle | None:
        stmt = select(BundleRecord).where(
            BundleRecord.workspace == workspace, BundleRecord.generation == generation
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None


__all__ = ["BundleRepository"]
