"""`knowledge_chats` の repository（S73）。DB 操作だけを担う。

**すべての問い合わせを (workspace, owner_uid) で絞る**（design C2: 画面と API では本人だけが
読み書き・削除できる）。ID だけで行を引く関数は置かない。誰が呼べるか・件数と大きさの上限・
監査は `services/knowledge_chat.py`。
時刻は `clock_timestamp()`（同じトランザクションの中でも進む）。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import KnowledgeChatRecord
from speclino.domain.knowledge_chat import (
    ChatMessage,
    KnowledgeChat,
    KnowledgeChatKind,
    KnowledgeChatSummary,
)

_MESSAGES: TypeAdapter[list[ChatMessage]] = TypeAdapter(list[ChatMessage])


def _to_domain(row: KnowledgeChatRecord) -> KnowledgeChat:
    return KnowledgeChat(
        id=str(row.id),
        workspace=row.workspace,
        owner_uid=row.owner_uid,
        kind=row.kind,
        artifact_id=str(row.artifact_id) if row.artifact_id is not None else None,
        title=row.title,
        messages=_MESSAGES.validate_python(row.messages),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _dump(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json", exclude_none=True) for m in messages]


def _owned(workspace: str, owner_uid: str) -> list[Any]:
    return [KnowledgeChatRecord.workspace == workspace, KnowledgeChatRecord.owner_uid == owner_uid]


class KnowledgeChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        workspace: str,
        owner_uid: str,
        kind: KnowledgeChatKind,
        artifact_id: uuid.UUID | None,
        title: str,
    ) -> KnowledgeChat:
        now = func.clock_timestamp()
        row = KnowledgeChatRecord(
            workspace=workspace,
            owner_uid=owner_uid,
            kind=kind,
            artifact_id=artifact_id,
            title=title,
            messages=[],
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_domain(row)

    async def list_for_owner(
        self,
        workspace: str,
        owner_uid: str,
        *,
        kind: KnowledgeChatKind | None = None,
        artifact_id: uuid.UUID | None = None,
    ) -> list[KnowledgeChatSummary]:
        """本人の会話（新しい順）。本文は読まない。"""
        cond = _owned(workspace, owner_uid)
        if kind is not None:
            cond.append(KnowledgeChatRecord.kind == kind)
        if artifact_id is not None:
            cond.append(KnowledgeChatRecord.artifact_id == artifact_id)
        stmt = (
            select(
                KnowledgeChatRecord.id,
                KnowledgeChatRecord.kind,
                KnowledgeChatRecord.artifact_id,
                KnowledgeChatRecord.title,
                func.jsonb_array_length(KnowledgeChatRecord.messages),
                KnowledgeChatRecord.version,
                KnowledgeChatRecord.updated_at,
            )
            .where(*cond)
            .order_by(KnowledgeChatRecord.updated_at.desc(), KnowledgeChatRecord.id)
        )
        return [
            KnowledgeChatSummary(
                id=str(cid),
                kind=k,
                artifact_id=str(aid) if aid is not None else None,
                title=title,
                message_count=count,
                version=version,
                updated_at=updated,
            )
            for cid, k, aid, title, count, version, updated in (
                await self._session.execute(stmt)
            ).all()
        ]

    def _select(self, workspace: str, owner_uid: str, chat_id: uuid.UUID) -> Select[Any]:
        return select(KnowledgeChatRecord).where(
            KnowledgeChatRecord.id == chat_id, *_owned(workspace, owner_uid)
        )

    async def get(
        self, workspace: str, owner_uid: str, chat_id: uuid.UUID, *, for_update: bool = False
    ) -> KnowledgeChat | None:
        stmt = self._select(workspace, owner_uid, chat_id)
        if for_update:
            stmt = stmt.with_for_update()
        # 同じセッションで更新した後も DB の値を読む（identity map の古い値を返さない）
        stmt = stmt.execution_options(populate_existing=True)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def update(
        self,
        workspace: str,
        owner_uid: str,
        chat_id: uuid.UUID,
        *,
        expected_version: int,
        **values: Any,
    ) -> KnowledgeChat | None:
        """題・結び付けを変える。持ち主でない・版が違えば None。"""
        stmt = (
            update(KnowledgeChatRecord)
            .where(
                KnowledgeChatRecord.id == chat_id,
                KnowledgeChatRecord.version == expected_version,
                *_owned(workspace, owner_uid),
            )
            .values(
                **values,
                version=KnowledgeChatRecord.version + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(KnowledgeChatRecord)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def save_messages(
        self,
        workspace: str,
        owner_uid: str,
        chat_id: uuid.UUID,
        messages: list[ChatMessage],
        *,
        title: str | None = None,
    ) -> KnowledgeChat | None:
        """発言の一覧を置き換え、版を進める。

        呼び出し側が `get(for_update=True)` で行を固定してから呼ぶ。
        """
        values: dict[str, Any] = {"messages": _dump(messages)}
        if title is not None:
            values["title"] = title
        stmt = (
            update(KnowledgeChatRecord)
            .where(KnowledgeChatRecord.id == chat_id, *_owned(workspace, owner_uid))
            .values(
                **values,
                version=KnowledgeChatRecord.version + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(KnowledgeChatRecord)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def delete(self, workspace: str, owner_uid: str, chat_id: uuid.UUID) -> bool:
        stmt = delete(KnowledgeChatRecord).where(
            KnowledgeChatRecord.id == chat_id, *_owned(workspace, owner_uid)
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_for_owner(self, workspace: str, owner_uid: str) -> int:
        """その人のワークスペースの会話をすべて消す（メンバーから外したとき）。消した件数。"""
        stmt = delete(KnowledgeChatRecord).where(*_owned(workspace, owner_uid))
        result = await self._session.execute(stmt)
        return int(result.rowcount)  # type: ignore[attr-defined]


__all__ = ["KnowledgeChatRepository"]
