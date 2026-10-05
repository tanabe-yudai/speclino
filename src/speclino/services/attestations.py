"""人の確定（S70、設計 6.2）。verdict は書き換えず、ゲートの処置として記録する。

- `attested`: その指紋・内容の hash の REVIEW をゲートの阻止から外す
- `acknowledged_violation`: 問題を認めたうえで阻止したままにする（記録だけ）

確定は指紋と内容の hash に結びつく。コードが変われば単位の hash（LLM 支援）や変わった
ファイルの hash（`KNOWLEDGE-BINDINGS-001`）が変わり、前の確定は効かない。確定できるのは
ワークスペースの owner / approver（REST）か、PR コメントの承認者（webhook）だけで、権限の
確認は呼び出し側で行う。ここでは記録と監査とゲートへの適用だけを行う。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.repositories.assessments import AttestationRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.assessment import Attestation, Disposition
from speclino.domain.finding import Finding, Findings
from speclino.services.llm_policies import CONTENT_HASH_REF, FINGERPRINT_REF

CONFIG_CHANGE_REQUIREMENT_ID = "KNOWLEDGE-BINDINGS-001"


def attestation_target(f: Finding) -> tuple[str, str] | None:
    """確定できる Finding なら (指紋, 内容の hash)。

    確定できるのは REVIEW で、指紋と hash を持つものだけ: LLM 支援の issue / split と、
    対応・契約・免除・規約の変更の通知（`KNOWLEDGE-BINDINGS-001`）。
    """
    if f.verdict != "REVIEW":
        return None
    if f.assessment_outcome not in ("issue", "split") and (
        f.requirement_id != CONFIG_CHANGE_REQUIREMENT_ID or f.assessment_outcome is not None
    ):
        return None
    fp = next(
        (r[len(FINGERPRINT_REF) :] for r in f.evidence_refs if r.startswith(FINGERPRINT_REF)), None
    )
    ch = next(
        (r[len(CONTENT_HASH_REF) :] for r in f.evidence_refs if r.startswith(CONTENT_HASH_REF)),
        None,
    )
    if not fp or not ch:
        return None
    return fp, ch


def informational_ids(findings: Findings) -> frozenset[str]:
    """LLM 支援の「指摘なし」。REVIEW だが情報としてだけ出し、ゲートを止めない。"""
    return frozenset(
        f.id
        for f in findings.findings
        if f.verdict == "REVIEW" and f.assessment_outcome == "no_issue"
    )


def _effective(attestations: list[Attestation]) -> dict[tuple[str, str], Attestation]:
    return {(a.fingerprint, a.content_hash): a for a in attestations}


def apply_attestations(findings: Findings, attestations: list[Attestation]) -> frozenset[str]:
    """阻止から外す Finding の ID（`attested` の確定が指紋と hash の両方で一致するもの）。"""
    return frozenset(f.id for f in findings.findings if _matching(f, attestations) is not None)


def applied_attestations(findings: Findings, attestations: list[Attestation]) -> list[Attestation]:
    """この Findings に効いた確定（`acknowledged_violation` も含む）。manifest に残す。"""
    by_key = _effective(attestations)
    used: dict[tuple[str, str], Attestation] = {}
    for f in findings.findings:
        target = attestation_target(f)
        if target is not None and target in by_key:
            used[target] = by_key[target]
    return [used[k] for k in sorted(used)]


def _matching(f: Finding, attestations: list[Attestation]) -> Attestation | None:
    target = attestation_target(f)
    if target is None:
        return None
    found = _effective(attestations).get(target)
    return found if found is not None and found.disposition == "attested" else None


async def attest(
    session: AsyncSession,
    *,
    fingerprint: str,
    content_hash: str,
    disposition: Disposition,
    actor: str,
    workspace: str,
    via: str = "api",
    now: datetime | None = None,
) -> Attestation:
    """確定を記録し（同じ指紋・hash は最新の処置で上書き）、監査に残す。"""
    # 入力の形を先に確かめる（hash の形式違反を DB に入れない）
    Attestation(
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        workspace=workspace,
        created_at=now or datetime.now().astimezone(),
    )
    saved = await AttestationRepository(session).upsert(
        workspace=workspace,
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        now=now,
    )
    await AuditRepository(session).record(
        actor,
        "attestation.record",
        f"{workspace}:{fingerprint}",
        disposition,
        {"content_hash": content_hash, "via": via},
    )
    return saved


async def list_attestations(session: AsyncSession, workspace: str) -> list[Attestation]:
    return await AttestationRepository(session).list_for_workspace(workspace)


__all__ = [
    "CONFIG_CHANGE_REQUIREMENT_ID",
    "CONTENT_HASH_REF",
    "FINGERPRINT_REF",
    "applied_attestations",
    "apply_attestations",
    "attest",
    "attestation_target",
    "informational_ids",
    "list_attestations",
]
