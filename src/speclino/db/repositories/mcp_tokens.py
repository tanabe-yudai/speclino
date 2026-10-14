"""`mcp_tokens` の repository（S54）。何を確かめて監査に残すかは `services/mcp_tokens.py`。"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import McpTokenRecord

# last_used_at はこれより古いときだけ書き直す（MCP の毎リクエストで書き込まないため）
_TOUCH_INTERVAL = timedelta(minutes=1)


class McpTokenRepository:
    """`mcp_tokens` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, team: str, name: str, token_hash: str, created_by: str
    ) -> McpTokenRecord:
        record = McpTokenRecord(team=team, name=name, token_hash=token_hash, created_by=created_by)
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return record

    async def list_for_team(self, team: str) -> list[McpTokenRecord]:
        stmt = (
            select(McpTokenRecord)
            .where(McpTokenRecord.team == team)
            .order_by(McpTokenRecord.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def revoke(self, *, token_id: uuid.UUID, team: str) -> bool:
        """team のトークンを失効させる。別のワークスペースの ID・既に失効済みなら False。"""
        stmt = (
            update(McpTokenRecord)
            .where(
                McpTokenRecord.id == token_id,
                McpTokenRecord.team == team,
                McpTokenRecord.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )
        result = await self._session.execute(stmt)
        return cast(CursorResult[Any], result).rowcount > 0

    async def find_active(self, token_hash: str) -> McpTokenRecord | None:
        stmt = select(McpTokenRecord).where(
            McpTokenRecord.token_hash == token_hash, McpTokenRecord.revoked_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def lock_active(self, *, token_id: uuid.UUID, team: str) -> McpTokenRecord | None:
        """有効なトークンの行をトランザクションの終わりまでロックして返す（失効と直列化する）。"""
        stmt = (
            select(McpTokenRecord)
            .where(
                McpTokenRecord.id == token_id,
                McpTokenRecord.team == team,
                McpTokenRecord.revoked_at.is_(None),
            )
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def touch(self, token_id: uuid.UUID) -> None:
        stmt = (
            update(McpTokenRecord)
            .where(
                McpTokenRecord.id == token_id,
                or_(
                    McpTokenRecord.last_used_at.is_(None),
                    McpTokenRecord.last_used_at < func.now() - _TOUCH_INTERVAL,
                ),
            )
            .values(last_used_at=func.now())
        )
        await self._session.execute(stmt)


__all__ = ["McpTokenRepository"]
