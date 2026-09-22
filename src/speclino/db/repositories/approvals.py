"""`approvals` テーブル: approve-spec / answer / attest の承認記録（サーバ側の正本）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Approval, ApprovalKind


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        kind: ApprovalKind,
        target: str,
        content_hash: str | None,
        approver: str,
        comment_url: str | None,
    ) -> Approval:
        row = Approval(
            project_id=project_id,
            kind=kind,
            target=target,
            content_hash=content_hash,
            approver=approver,
            comment_url=comment_url,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_project(
        self, project_id: uuid.UUID, kind: ApprovalKind | None = None
    ) -> list[Approval]:
        stmt = select(Approval).where(Approval.project_id == project_id)
        if kind is not None:
            stmt = stmt.where(Approval.kind == kind)
        result = await self._session.execute(stmt.order_by(Approval.created_at))
        return list(result.scalars().all())
