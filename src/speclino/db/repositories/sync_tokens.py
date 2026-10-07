"""`sync_tokens` / `knowledge_fetches` / `sync_plans` の repository（S62・S68・S75）。

`mcp_tokens` と同じ作り: 平文は保存せず SHA-256 だけ、失効は `revoked_at`、最終利用時刻は
間隔を置いて書く。トークンは 1 つのワークスペースの 1 つのプロジェクトに限る。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import KnowledgeFetchRecord, Project, SyncPlanRecord, SyncTokenRecord

_TOUCH_INTERVAL = timedelta(minutes=1)


class SyncTokenRepository:
    """`sync_tokens` テーブルの読み書き。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, team: str, project_id: uuid.UUID, name: str, token_hash: str, created_by: str
    ) -> SyncTokenRecord:
        record = SyncTokenRecord(
            team=team,
            project_id=project_id,
            name=name,
            token_hash=token_hash,
            created_by=created_by,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return record

    async def list_for_team(self, team: str) -> list[tuple[SyncTokenRecord, str]]:
        """team のトークンと、その対象リポジトリの名前（新しい順）。"""
        stmt = (
            select(SyncTokenRecord, Project.repo_full_name)
            .join(Project, Project.id == SyncTokenRecord.project_id)
            .where(SyncTokenRecord.team == team)
            .order_by(SyncTokenRecord.created_at.desc())
        )
        return [(r, name) for r, name in (await self._session.execute(stmt)).tuples()]

    async def find_active(self, token_hash: str) -> SyncTokenRecord | None:
        stmt = select(SyncTokenRecord).where(
            SyncTokenRecord.token_hash == token_hash, SyncTokenRecord.revoked_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def revoke(self, *, token_id: uuid.UUID, team: str) -> bool:
        """team のトークンを失効させる。別のワークスペースの ID・失効済みなら False。"""
        stmt = (
            update(SyncTokenRecord)
            .where(
                SyncTokenRecord.id == token_id,
                SyncTokenRecord.team == team,
                SyncTokenRecord.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )
        result = await self._session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount > 0

    async def touch(self, token_id: uuid.UUID) -> None:
        stmt = (
            update(SyncTokenRecord)
            .where(
                SyncTokenRecord.id == token_id,
                or_(
                    SyncTokenRecord.last_used_at.is_(None),
                    SyncTokenRecord.last_used_at < func.now() - _TOUCH_INTERVAL,
                ),
            )
            .values(last_used_at=func.now())
        )
        await self._session.execute(stmt)


class KnowledgeFetchRepository:
    """`knowledge_fetches` への追記。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        workspace: str,
        channel: str,
        bundle_generation: int,
        artifact_ids: list[str],
        token_id: uuid.UUID | None = None,
        uid: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> KnowledgeFetchRecord:
        record = KnowledgeFetchRecord(
            workspace=workspace,
            channel=channel,
            token_id=token_id,
            uid=uid,
            bundle_generation=bundle_generation,
            project_id=project_id,
            artifact_ids=artifact_ids,
        )
        self._session.add(record)
        await self._session.flush()
        return record


class SyncPlanRepository:
    """`sync_plans`: 使い捨ての取得 URL の計画（S75）。トークンは SHA-256 だけを扱う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: SyncPlanRecord, *, now: datetime) -> None:
        # 期限を 1 日過ぎたものは片付ける（取得済み・失効済みの本文を長く持たない）
        await self._session.execute(
            delete(SyncPlanRecord).where(SyncPlanRecord.expires_at < now - timedelta(days=1))
        )
        self._session.add(record)
        await self._session.flush()

    async def consume(self, token_hash: str, *, now: datetime) -> SyncPlanRecord | None:
        """未取得・期限内なら取得済みにして返す（1 回だけ。同時の取得はどちらか一方だけ通る）。"""
        result = await self._session.execute(
            update(SyncPlanRecord)
            .where(
                SyncPlanRecord.token_hash == token_hash,
                SyncPlanRecord.consumed_at.is_(None),
                SyncPlanRecord.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(SyncPlanRecord)
        )
        return result.scalar_one_or_none()


__all__ = ["KnowledgeFetchRepository", "SyncPlanRepository", "SyncTokenRepository"]
