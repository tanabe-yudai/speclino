"""`llm_assessments` と `attestations` の repository（S70）。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
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
        created_at=row.created_at,
        self_attested=row.self_attested,
    )


class AttestationRepository:
    """人の確定。同じ (workspace, fingerprint, content_hash) は最新の処置で上書きする。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        workspace: str,
        fingerprint: str,
        content_hash: str,
        disposition: Disposition,
        actor: str,
        self_attested: bool | None = None,
        now: datetime | None = None,
    ) -> Attestation:
        created_at = now or datetime.now(UTC)
        stmt = (
            insert(AttestationRecord)
            .values(
                workspace=workspace,
                fingerprint=fingerprint,
                content_hash=content_hash,
                disposition=disposition,
                actor=actor,
                self_attested=self_attested,
                created_at=created_at,
            )
            .on_conflict_do_update(
                constraint="uq_attestations_workspace_fingerprint_hash",
                set_={
                    "disposition": disposition,
                    "actor": actor,
                    "self_attested": self_attested,
                    "created_at": created_at,
                },
            )
            .returning(AttestationRecord)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        await self._session.flush()
        return _attestation(row)

    async def list_for_workspace(self, workspace: str) -> list[Attestation]:
        stmt = (
            select(AttestationRecord)
            .where(AttestationRecord.workspace == workspace)
            .order_by(AttestationRecord.fingerprint, AttestationRecord.content_hash)
        )
        return [_attestation(r) for r in (await self._session.execute(stmt)).scalars()]


__all__ = ["AssessmentRepository", "AttestationRepository", "SessionFactoryAssessmentStore"]
