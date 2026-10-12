"""`artifacts` / `artifact_revisions` の repository（S62）。

DB 操作だけを担う。誰が承認できるか・何を監査に残すか・競合の検査は
`services/knowledge_registry.py`。返り値は domain の `ArtifactRevision`（artifact 行の
workspace / kind / name を合わせて組み立てる）。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import ArtifactRecord, ArtifactRevisionRecord
from speclino.domain.knowledge import (
    ArtifactContent,
    ArtifactKind,
    ArtifactRevision,
    ArtifactScope,
)

_CONTENT: TypeAdapter[Any] = TypeAdapter(ArtifactContent)


def _to_domain(artifact: ArtifactRecord, row: ArtifactRevisionRecord) -> ArtifactRevision:
    return ArtifactRevision(
        artifact_id=str(artifact.id),
        workspace=artifact.workspace,
        kind=artifact.kind,
        name=artifact.name,
        revision=row.revision,
        status=row.status,
        content=_CONTENT.validate_python(row.content),
        content_hash=row.content_hash,
        author=row.author,
        approver=row.approver,
        self_approved=row.self_approved,
        note=row.note,
        note_source=row.note_source,
        created_at=row.created_at,
        approved_at=row.approved_at,
    )


def _joined() -> Select[tuple[ArtifactRecord, ArtifactRevisionRecord]]:
    return select(ArtifactRecord, ArtifactRevisionRecord).join(
        ArtifactRevisionRecord, ArtifactRevisionRecord.artifact_id == ArtifactRecord.id
    )


class ArtifactRepository:
    """`artifacts` と `artifact_revisions` の読み書き。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, artifact_id: uuid.UUID) -> ArtifactRecord | None:
        return await self._session.get(ArtifactRecord, artifact_id)

    async def find(self, workspace: str, kind: ArtifactKind, name: str) -> ArtifactRecord | None:
        stmt = select(ArtifactRecord).where(
            ArtifactRecord.workspace == workspace,
            ArtifactRecord.kind == kind,
            ArtifactRecord.name == name,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(self, *, workspace: str, kind: ArtifactKind, name: str) -> ArtifactRecord:
        record = ArtifactRecord(workspace=workspace, kind=kind, name=name)
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_workspace(self, workspace: str) -> list[ArtifactRecord]:
        stmt = (
            select(ArtifactRecord)
            .where(ArtifactRecord.workspace == workspace)
            .order_by(ArtifactRecord.kind, ArtifactRecord.name)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def revision_numbers(self, workspace: str) -> dict[uuid.UUID, dict[str, int]]:
        """artifact ごとの active / draft の改訂番号と、superseded の最大の番号（一覧の表示用）。"""
        stmt = (
            select(
                ArtifactRevisionRecord.artifact_id,
                ArtifactRevisionRecord.status,
                func.max(ArtifactRevisionRecord.revision),
            )
            .join(ArtifactRecord, ArtifactRecord.id == ArtifactRevisionRecord.artifact_id)
            .where(ArtifactRecord.workspace == workspace)
            .group_by(ArtifactRevisionRecord.artifact_id, ArtifactRevisionRecord.status)
        )
        out: dict[uuid.UUID, dict[str, int]] = {}
        for aid, status, revision in (await self._session.execute(stmt)).all():
            out.setdefault(aid, {})[status] = revision
        return out

    async def revision_scopes(self, workspace: str) -> dict[uuid.UUID, dict[str, ArtifactScope]]:
        """artifact ごとの active / draft のスコープ（一覧の絞り込み用。本文は読まない）。"""
        stmt = (
            select(
                ArtifactRevisionRecord.artifact_id,
                ArtifactRevisionRecord.status,
                ArtifactRevisionRecord.content["scope"],
            )
            .join(ArtifactRecord, ArtifactRecord.id == ArtifactRevisionRecord.artifact_id)
            .where(
                ArtifactRecord.workspace == workspace,
                ArtifactRevisionRecord.status.in_(("active", "draft")),
            )
        )
        out: dict[uuid.UUID, dict[str, ArtifactScope]] = {}
        for aid, status, scope in (await self._session.execute(stmt)).all():
            out.setdefault(aid, {})[status] = ArtifactScope.model_validate(scope)
        return out

    async def current_draft(self, artifact_id: uuid.UUID) -> ArtifactRevision | None:
        return await self._one(artifact_id, ArtifactRevisionRecord.status == "draft")

    async def current_active(self, artifact_id: uuid.UUID) -> ArtifactRevision | None:
        return await self._one(artifact_id, ArtifactRevisionRecord.status == "active")

    async def latest(self, artifact_id: uuid.UUID) -> ArtifactRevision | None:
        stmt = (
            _joined()
            .where(ArtifactRecord.id == artifact_id)
            .order_by(ArtifactRevisionRecord.revision.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        return _to_domain(*row) if row is not None else None

    async def revision_at(self, artifact_id: uuid.UUID, revision: int) -> ArtifactRevision | None:
        return await self._one(artifact_id, ArtifactRevisionRecord.revision == revision)

    async def list_revisions(self, artifact_id: uuid.UUID) -> list[ArtifactRevision]:
        """新しい順。"""
        stmt = (
            _joined()
            .where(ArtifactRecord.id == artifact_id)
            .order_by(ArtifactRevisionRecord.revision.desc())
        )
        return [_to_domain(a, r) for a, r in (await self._session.execute(stmt)).all()]

    async def active_in_workspace(self, workspace: str) -> list[ArtifactRevision]:
        """workspace の active な改訂（kind・名前の順）。"""
        stmt = (
            _joined()
            .where(ArtifactRecord.workspace == workspace, ArtifactRevisionRecord.status == "active")
            .order_by(ArtifactRecord.kind, ArtifactRecord.name)
        )
        return [_to_domain(a, r) for a, r in (await self._session.execute(stmt)).all()]

    async def add_draft(
        self,
        *,
        artifact_id: uuid.UUID,
        revision: int,
        content: dict[str, Any],
        content_hash: str,
        author: str,
        note: str = "",
        note_source: str | None = None,
    ) -> ArtifactRevision:
        row = ArtifactRevisionRecord(
            artifact_id=artifact_id,
            revision=revision,
            status="draft",
            content=content,
            content_hash=content_hash,
            author=author,
            self_approved=False,
            note=note,
            note_source=note_source if note else None,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        artifact = await self.get(artifact_id)
        assert artifact is not None
        return _to_domain(artifact, row)

    async def update_draft(
        self,
        *,
        artifact_id: uuid.UUID,
        revision: int,
        content: dict[str, Any],
        content_hash: str,
        note: str = "",
        note_source: str | None = None,
    ) -> ArtifactRevision | None:
        """下書きの中身と改訂の説明（note）を置き換える。draft でなくなっていれば None。"""
        stmt = (
            update(ArtifactRevisionRecord)
            .where(
                ArtifactRevisionRecord.artifact_id == artifact_id,
                ArtifactRevisionRecord.revision == revision,
                ArtifactRevisionRecord.status == "draft",
            )
            .values(
                content=content,
                content_hash=content_hash,
                note=note,
                note_source=note_source if note else None,
            )
            .returning(ArtifactRevisionRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        artifact = await self.get(artifact_id)
        return _to_domain(artifact, row) if row is not None and artifact is not None else None

    async def approve(
        self,
        *,
        artifact_id: uuid.UUID,
        revision: int,
        approver: str,
        self_approved: bool,
        content: dict[str, Any],
        content_hash: str,
    ) -> ArtifactRevision | None:
        """draft を active にし、それまでの active を superseded にする。

        `content` / `content_hash` は承認時に確定した内容（spec はサーバが作った IR 入り）。

        active 化は `status='draft'` を条件にし、0 行なら None（並行する承認で状態が変わった。
        `PolicyRevisionRepository.approve` と同じ TOCTOU 対策）。先に行った superseded 化は
        呼び出し側の rollback で戻す前提（active の部分一意インデックスのため順序はこの通り）。
        """
        await self._session.execute(
            update(ArtifactRevisionRecord)
            .where(
                ArtifactRevisionRecord.artifact_id == artifact_id,
                ArtifactRevisionRecord.status == "active",
            )
            .values(status="superseded")
        )
        stmt = (
            update(ArtifactRevisionRecord)
            .where(
                ArtifactRevisionRecord.artifact_id == artifact_id,
                ArtifactRevisionRecord.revision == revision,
                ArtifactRevisionRecord.status == "draft",
            )
            .values(
                status="active",
                approver=approver,
                self_approved=self_approved,
                content=content,
                content_hash=content_hash,
                approved_at=func.now(),
            )
            .returning(ArtifactRevisionRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        artifact = await self.get(artifact_id)
        return _to_domain(artifact, row) if row is not None and artifact is not None else None

    async def retire(self, *, artifact_id: uuid.UUID, revision: int) -> ArtifactRevision | None:
        """active な改訂を superseded にする（後継の active は作らない）。

        `status='active'` と番号を条件にし、0 行なら None（並行する承認・取り下げで変わった）。
        """
        stmt = (
            update(ArtifactRevisionRecord)
            .where(
                ArtifactRevisionRecord.artifact_id == artifact_id,
                ArtifactRevisionRecord.revision == revision,
                ArtifactRevisionRecord.status == "active",
            )
            .values(status="superseded")
            .returning(ArtifactRevisionRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        artifact = await self.get(artifact_id)
        return _to_domain(artifact, row) if row is not None and artifact is not None else None

    async def _one(self, artifact_id: uuid.UUID, cond: Any) -> ArtifactRevision | None:
        stmt = _joined().where(ArtifactRecord.id == artifact_id, cond)
        row = (await self._session.execute(stmt)).first()
        return _to_domain(*row) if row is not None else None


__all__ = ["ArtifactRepository"]
