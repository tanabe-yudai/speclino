"""`llm_assessments` と `attestations` の repository（S70）。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

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

    同じキーは最初の記録が正本（`ON CONFLICT DO NOTHING`）。並行する run が同じキーを同時に
    評価しても、片方の記録だけが残り、以後はそれを返す。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: AssessmentKey) -> AssessmentRecord | None:
        stmt = select(LlmAssessmentRecord).where(LlmAssessmentRecord.key == key.as_str())
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
                key=record.key.as_str(),
                key_fields=record.key.model_dump(mode="json"),
                unit_symbol=record.unit_symbol,
                items=[i.model_dump(mode="json") for i in record.items],
                created_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await self._session.execute(stmt)
        await self._session.flush()


def _attestation(row: AttestationRecord) -> Attestation:
    return Attestation(
        fingerprint=row.fingerprint,
        content_hash=row.content_hash,
        disposition=row.disposition,  # type: ignore[arg-type]
        actor=row.actor,
        workspace=row.workspace,
        created_at=row.created_at,
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
                created_at=created_at,
            )
            .on_conflict_do_update(
                constraint="uq_attestations_workspace_fingerprint_hash",
                set_={"disposition": disposition, "actor": actor, "created_at": created_at},
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


__all__ = ["AssessmentRepository", "AttestationRepository"]
