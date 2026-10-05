"""人の確定（S70、設計 6.2）。verdict は書き換えず、ゲートの処置として記録する。

- `attested`: その指紋・内容の hash の REVIEW をゲートの阻止から外す
- `acknowledged_violation`: 問題を認めたうえで阻止したままにする（記録だけ）

確定は指紋と内容の hash に結びつく。コードが変われば単位の hash（LLM 支援）や変わった
ファイルの hash（`KNOWLEDGE-BINDINGS-001`）が変わり、前の確定は効かない。確定できるのは
ワークスペースの owner / approver（REST）か、PR コメントの承認者（webhook）だけで、権限の
確認は呼び出し側で行う。ここでは記録と監査とゲートへの適用だけを行う。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project, ReviewRun
from speclino.db.repositories import ProjectRepository, ReviewRunRepository
from speclino.db.repositories.assessments import AttestationRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.assessment import Attestation, Disposition
from speclino.domain.finding import Finding, Findings
from speclino.domain.workspace import APPROVER_ROLES
from speclino.services.llm_policies import CONTENT_HASH_REF, FINGERPRINT_REF
from speclino.services.workspaces import WorkspaceNotFound, role_of

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
    self_attested: bool | None = None,
    now: datetime | None = None,
) -> Attestation:
    """確定を記録し（同じ指紋・hash は最新の処置で上書き）、監査に残す。

    `self_attested`: PR の作成者が自分で確定したか（GitHub のユーザー ID で照合）。1 人での運用
    （仕様の自己承認と同じ）のため拒否はしないが、監査・gate.json・manifest に印を残す。None は
    「作成者との照合不可」で、false と区別して残す。対応・規約の変更（KNOWLEDGE-BINDINGS-001）の
    確定で true / None のときは監査に警告を足す（裁定 34: 拒否はしない）。
    """
    # 入力の形を先に確かめる（hash の形式違反を DB に入れない）
    Attestation(
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        workspace=workspace,
        created_at=now or datetime.now().astimezone(),
    )
    saved, first_actor = await AttestationRepository(session).upsert(
        workspace=workspace,
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        self_attested=self_attested,
        now=now,
    )
    detail: dict[str, object] = {
        "content_hash": content_hash,
        "via": via,
        # この確定の照合結果と、保存した印（true > null > false で強い方を保つ）
        "self_attested": self_attested,
        "self_attested_label": self_attest_label(self_attested),
        "self_attested_stored": saved.self_attested,
        "first_actor": first_actor,
        "latest_actor": actor,
    }
    warning = config_change_warning(fingerprint, self_attested)
    if warning is not None:
        detail["warning"] = warning
    await AuditRepository(session).record(
        actor, "attestation.record", f"{workspace}:{fingerprint}", disposition, detail
    )
    return saved


def same_github_user(actor_id: int | None, author_id: int | None) -> bool | None:
    """確定者と PR の作成者が同じ GitHub ユーザーか。どちらかの ID が無ければ None（照合不可）。"""
    if actor_id is None or author_id is None:
        return None
    return actor_id == author_id


def self_attest_label(self_attested: bool | None) -> str:
    if self_attested is None:
        return "作成者との照合不可"
    return "自己確定" if self_attested else "作成者以外の確定"


def config_change_warning(fingerprint: str, self_attested: bool | None) -> str | None:
    """対応・契約・免除・規約の変更の確定を、作成者自身か照合不可の人が行ったときの警告。"""
    if not fingerprint.startswith(f"{CONFIG_CHANGE_REQUIREMENT_ID}:") or self_attested is False:
        return None
    who = "PR の作成者自身" if self_attested else "PR の作成者と照合できない人"
    return (
        f"{who}が対応・契約・免除・規約の変更を確定した。判定の前提を変える変更なので、"
        "別の人によるレビューを推奨する"
    )


BINDINGS_VIA_PR_COMMENT_REASON = "対応表・免除・規約の変更の確定は PR コメントで行う"


class BindingsAttestRequiresPrComment(PermissionError):
    """KNOWLEDGE-BINDINGS-001 の確定を REST で行おうとした（裁定 35。PR コメントだけ受け付ける）。

    画面の uid は GitHub のアカウントと対応しておらず PR の作成者と照合できない。判定の前提を
    変える確定は、作成者との照合ができる経路に限る。
    """


# SpecLino の uid → 連携済みの GitHub ユーザー ID（未連携なら None）
GithubIdOf = Callable[[str], Awaitable[int | None]]


async def attest_as_member(
    session: AsyncSession,
    *,
    workspace: str,
    uid: str,
    fingerprint: str,
    content_hash: str,
    disposition: Disposition,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
    run_id: uuid.UUID | None = None,
    github_id_of: GithubIdOf | None = None,
) -> tuple[Attestation, tuple[Project, ReviewRun] | None]:
    """画面（REST）からの確定。owner / approver だけ。戻り値は (確定, 再検査する (project, run))。

    `run_id`（確定した画面の run）があれば、その PR の最新の run を再検査の対象にする。
    自己確定は GitHub のユーザー ID で照合する: 確定者の uid → 連携済みの GitHub ID
    （`github_id_of`）と、run の PR の作成者の ID（`pr_author_id`）。どちらかが無い（未連携・
    run 無し・作成者の ID の無い run）なら None（照合不可）。

    メンバーでなければ（別のワークスペースも）`WorkspaceNotFound`（404、存在を漏らさない）、
    member なら `PermissionError`（403）。拒否は `audit_factory` の別トランザクションで監査に残す
    （呼び出し側のトランザクションは例外で巻き戻るため）。
    """
    role = await role_of(session, workspace, uid)
    if role is None:
        raise WorkspaceNotFound(workspace)
    reject: Exception | None = None
    if role not in APPROVER_ROLES:
        reject = PermissionError(f"{uid} は {workspace} の owner / approver ではない")
    elif fingerprint.startswith(f"{CONFIG_CHANGE_REQUIREMENT_ID}:"):
        # 裁定 35: 信頼側の設定の変更は、作成者との照合（GitHub の ID）ができる PR コメントだけ
        reject = BindingsAttestRequiresPrComment(BINDINGS_VIA_PR_COMMENT_REASON)
    if reject is not None:
        if audit_factory is not None:
            async with audit_factory() as s, s.begin():
                await AuditRepository(s).record(
                    uid,
                    "attestation.record",
                    f"{workspace}:{fingerprint}",
                    "rejected",
                    {"content_hash": content_hash, "via": "api", "reason": str(reject)},
                )
        raise reject
    target = await recheck_target(session, workspace, run_id) if run_id is not None else None
    saved = await attest(
        session,
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=uid,
        workspace=workspace,
        via="api",
        self_attested=await _self_attested(uid, target, github_id_of),
    )
    return saved, target


async def _self_attested(
    uid: str, target: tuple[Project, ReviewRun] | None, github_id_of: GithubIdOf | None
) -> bool | None:
    if target is None or github_id_of is None:
        return None
    return same_github_user(await github_id_of(uid), target[1].pr_author_id)


def self_attested_ids(
    findings: Findings, attestations: list[Attestation], mark: bool | None
) -> list[str]:
    """attested の確定で阻止から外れた Finding のうち、自己確定の印が `mark` のものの ID。

    mark=True は自己確定、None は作成者との照合不可。どちらも gate.json に残す。
    """
    return sorted(
        f.id
        for f in findings.findings
        if (a := _matching(f, attestations)) is not None and a.self_attested is mark
    )


async def recheck_target(
    session: AsyncSession, workspace: str, run_id: uuid.UUID
) -> tuple[Project, ReviewRun]:
    """確定のあとに再検査する (project, その PR の最新の run)。

    run がこのワークスペースのプロジェクトのものでなければ LookupError（404。存在を漏らさない）。
    """
    runs = ReviewRunRepository(session)
    run = await runs.get(run_id)
    project = await ProjectRepository(session).get(run.project_id) if run is not None else None
    if run is None or project is None or project.team != workspace:
        raise LookupError(f"run {run_id} が無い")
    latest = await runs.latest_run_for_pr(project.id, run.pr_number) if run.pr_number else None
    return project, latest or run


NOT_IN_PR_REASON = "この PR の検査に無い指紋"


async def in_pr_findings(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    pr_number: int,
    fingerprint: str,
    content_hash: str,
) -> bool:
    """(指紋, hash) がその PR の最新の完了した run の確定できる Finding にあるか（S70）。

    PR コメントの確定はそのコメントが付いた PR の指摘に限る。他の PR の指摘を別の PR のコメントで
    確定させると、自己確定の照合相手（この PR の作成者）と効く対象（別の PR）がずれる。
    """
    from speclino.db.repositories import FindingRepository

    run = await ReviewRunRepository(session).latest_completed_for_pr(project_id, pr_number)
    if run is None:
        return False
    for row in await FindingRepository(session).list_for_run(run.id):
        if attestation_target(Finding.model_validate(row.payload)) == (fingerprint, content_hash):
            return True
    return False


async def list_attestations(session: AsyncSession, workspace: str) -> list[Attestation]:
    return await AttestationRepository(session).list_for_workspace(workspace)


__all__ = [
    "CONFIG_CHANGE_REQUIREMENT_ID",
    "CONTENT_HASH_REF",
    "FINGERPRINT_REF",
    "NOT_IN_PR_REASON",
    "applied_attestations",
    "apply_attestations",
    "attest",
    "attest_as_member",
    "in_pr_findings",
    "attestation_target",
    "informational_ids",
    "list_attestations",
    "BINDINGS_VIA_PR_COMMENT_REASON",
    "BindingsAttestRequiresPrComment",
    "GithubIdOf",
    "config_change_warning",
    "same_github_user",
    "self_attest_label",
    "recheck_target",
    "self_attested_ids",
]
