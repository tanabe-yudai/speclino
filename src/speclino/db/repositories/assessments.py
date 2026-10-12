"""`llm_assessments` と `attestations` の repository（S70）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, false, null, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import AttestationRecord, LlmAssessmentRecord
from speclino.domain.assessment import (
    AssessmentKey,
    AssessmentRecord,
    Attestation,
    Disposition,
    ItemVerdict,
)


class AssessmentRepository:
    """LLM 支援の評価の記録（`services.llm_policies.AssessmentStore` の実体）。

    記録はワークスペースごとに区切る（同じコードでも別のワークスペースの評価は共有しない。
    あるワークスペースの記録で別のワークスペースのゲートが決まらないように）。
    同じ (workspace, キー) は最初の記録が正本（`ON CONFLICT DO NOTHING`）。並行する run が同じ
    キーを同時に評価しても、片方の記録だけが残り、以後はそれを返す。
    """

    def __init__(self, session: AsyncSession, workspace: str) -> None:
        self._session = session
        self._workspace = workspace

    async def get(self, key: AssessmentKey) -> AssessmentRecord | None:
        stmt = select(LlmAssessmentRecord).where(
            LlmAssessmentRecord.workspace == self._workspace,
            LlmAssessmentRecord.key == key_str(key),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return AssessmentRecord(
            key=AssessmentKey.model_validate(row.key_fields),
            unit_symbol=row.unit_symbol,
            items=[ItemVerdict.model_validate(i) for i in row.items],
            created_at=row.created_at,
        )

    async def put(self, record: AssessmentRecord) -> None:
        stmt = (
            insert(LlmAssessmentRecord)
            .values(
                workspace=self._workspace,
                key=key_str(record.key),
                key_fields=record.key.model_dump(mode="json"),
                unit_symbol=record.unit_symbol,
                items=[i.model_dump(mode="json") for i in record.items],
                created_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=["workspace", "key"])
        )
        await self._session.execute(stmt)
        await self._session.flush()


class SessionFactoryAssessmentStore:
    """読み書きごとに短いトランザクションを開く store（worker 用）。

    LLM の呼出し（数秒〜数十秒）の間 DB の接続を握らないため、`AssessmentRepository` を
    呼出しごとに作る。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], workspace: str) -> None:
        self._factory = factory
        self._workspace = workspace

    async def get(self, key: AssessmentKey) -> AssessmentRecord | None:
        async with self._factory() as session:
            return await AssessmentRepository(session, self._workspace).get(key)

    async def put(self, record: AssessmentRecord) -> None:
        async with self._factory() as session, session.begin():
            await AssessmentRepository(session, self._workspace).put(record)


def key_str(key: AssessmentKey) -> str:
    # llm_policies.key_str と同じ正規 JSON（services → db の循環を避けて遅延 import）
    from speclino.services.llm_policies import key_str as canonical

    return canonical(key)


def _attestation(row: AttestationRecord) -> Attestation:
    return Attestation(
        fingerprint=row.fingerprint,
        content_hash=row.content_hash,
        disposition=row.disposition,
        actor=row.actor,
        workspace=row.workspace,
        project_id=str(row.project_id),
        created_at=row.created_at,
        self_attested=row.self_attested,
    )


class AttestationRepository:
    """人の確定。同じ (workspace, project_id, fingerprint, content_hash) は最新で上書きする。

    確定はプロジェクトに属する（#114）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        workspace: str,
        project_id: uuid.UUID,
        fingerprint: str,
        content_hash: str,
        disposition: Disposition,
        actor: str,
        self_attested: bool | None = None,
        now: datetime | None = None,
    ) -> tuple[Attestation, str]:
        """確定を記録し、(保存後の確定, 最初の確定者) を返す。

        確定し直すと処置・確定者・時刻は最新にするが、自己確定の印は「true > null > false」で
        強い方を保つ（別の人の確定し直しで自己確定・照合不可の印が消えない）。最初の確定者は変えない。
        """
        created_at = now or datetime.now(UTC)
        table = AttestationRecord.__table__
        base = insert(AttestationRecord).values(
            workspace=workspace,
            project_id=project_id,
            fingerprint=fingerprint,
            content_hash=content_hash,
            disposition=disposition,
            actor=actor,
            first_actor=actor,
            self_attested=self_attested,
            created_at=created_at,
        )
        old, new = table.c.self_attested, base.excluded.self_attested
        merged = case(
            (or_(old.is_(True), new.is_(True)), true()),
            (or_(old.is_(None), new.is_(None)), null()),
            else_=false(),
        )
        stmt = (
            base.on_conflict_do_update(
                constraint="uq_attestations_workspace_project_fingerprint_hash",
                set_={
                    "disposition": disposition,
                    "actor": actor,
                    "self_attested": merged,
                    "created_at": created_at,
                },
            )
            .returning(AttestationRecord)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        await self._session.flush()
        return _attestation(row), row.first_actor

    async def insert_if_absent(
        self,
        *,
        workspace: str,
        project_id: uuid.UUID,
        fingerprint: str,
        content_hash: str,
        actor: str,
        self_attested: bool | None = None,
        now: datetime | None = None,
    ) -> Attestation | None:
        """attested の確定を、同じ (指紋, hash) の確定が無いときだけ記録する（原子的）。

        既にあれば（attested でも acknowledged_violation でも、並行して保存されたものでも）何も
        変えずに None を返す。まとめての確定が人の個別の処置を覆さないため。
        """
        stmt = (
            insert(AttestationRecord)
            .values(
                workspace=workspace,
                project_id=project_id,
                fingerprint=fingerprint,
                content_hash=content_hash,
                disposition="attested",
                actor=actor,
                first_actor=actor,
                self_attested=self_attested,
                created_at=now or datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_attestations_workspace_project_fingerprint_hash")
            .returning(AttestationRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        await self._session.flush()
        return _attestation(row) if row is not None else None

    async def list_for_project(self, workspace: str, project_id: uuid.UUID) -> list[Attestation]:
        """そのプロジェクトの確定（run のゲートに効かせるのはこれだけ。#114）。"""
        stmt = (
            select(AttestationRecord)
            .where(
                AttestationRecord.workspace == workspace,
                AttestationRecord.project_id == project_id,
            )
            .order_by(AttestationRecord.fingerprint, AttestationRecord.content_hash)
        )
        return [_attestation(r) for r in (await self._session.execute(stmt)).scalars()]


__all__ = ["AssessmentRepository", "AttestationRepository", "SessionFactoryAssessmentStore"]
