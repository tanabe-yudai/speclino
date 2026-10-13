"""人の確定（S70）。verdict は書き換えず、ゲートの処置として記録する。

- `attested`: その指紋・内容の hash の REVIEW をゲートの阻止から外す
- `acknowledged_violation`: 問題を認めたうえで阻止したままにする（記録だけ）

確定は指紋と内容の hash に結びつき、プロジェクトに属する（#114: 別のプロジェクトの run には
効かない）。コードが変われば単位の hash（LLM 支援）や変わった
ファイルの hash（`KNOWLEDGE-BINDINGS-001`）が変わり、前の確定は効かない。確定できるのは
ワークスペースの owner / approver（REST）か、PR コメントの承認者（webhook）だけで、権限の
確認は呼び出し側で行う。ここでは記録と監査とゲートへの適用だけを行う。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project, ReviewRun
from speclino.db.repositories import ProjectRepository, ReviewRunRepository
from speclino.db.repositories.assessments import AttestationRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.assessment import Attestation, Disposition
from speclino.domain.finding import CONTENT_HASH_REF, FINGERPRINT_REF, Finding, Findings
from speclino.domain.workspace import APPROVER_ROLES
from speclino.services.approvers import is_approver
from speclino.services.workspaces import WorkspaceNotFound, role_of

CONFIG_CHANGE_REQUIREMENT_ID = "KNOWLEDGE-BINDINGS-001"


def attestation_target(f: Finding) -> tuple[str, str] | None:
    """確定できる Finding なら (指紋, 内容の hash)。

    確定できるのは REVIEW で、指紋と hash を持つものだけ: LLM 支援のすべての区分（issue / split /
    no_issue。no_issue も機械検証ではないので、必須の規約では人の確定まで阻止する。#114）と、
    対応・契約・免除・規約の変更の通知（`KNOWLEDGE-BINDINGS-001`）と、ワークスペース全体の仕様の図が
    この変更で対象外になった通知（`scope_changed`）。
    """
    if f.verdict != "REVIEW":
        return None
    if (
        f.assessment_outcome is None
        and f.requirement_id != CONFIG_CHANGE_REQUIREMENT_ID
        and f.category != "scope_changed"
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
    project_id: uuid.UUID,
    via: str = "api",
    self_attested: bool | None = None,
    actor_login: str | None = None,
    now: datetime | None = None,
) -> Attestation:
    """確定を記録し（同じ指紋・hash は最新の処置で上書き）、監査に残す。

    `self_attested`: PR の作成者が自分で確定したか（GitHub のユーザー ID で照合）。1 人での運用
    （仕様の自己承認と同じ）のため拒否はしないが、監査・gate.json・manifest に印を残す。None は
    「作成者との照合不可」で、false と区別して残す。対応・規約の変更（KNOWLEDGE-BINDINGS-001）の
    確定で true / None のときは監査に警告を足す（裁定 34: 拒否はしない）。
    `actor_login`: PR コメントの確定で actor（`github:<ユーザー ID>`）に添えて監査に残す
    ログイン名。
    """
    # 入力の形を先に確かめる（hash の形式違反を DB に入れない）
    Attestation(
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        workspace=workspace,
        project_id=str(project_id),
        created_at=now or datetime.now().astimezone(),
    )
    saved, first_actor = await AttestationRepository(session).upsert(
        workspace=workspace,
        project_id=project_id,
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=actor,
        self_attested=self_attested,
        now=now,
    )
    await _audit_attestation(session, saved, first_actor, via, self_attested, actor_login)
    return saved


async def attest_if_absent(
    session: AsyncSession,
    *,
    fingerprint: str,
    content_hash: str,
    actor: str,
    workspace: str,
    project_id: uuid.UUID,
    via: str,
    self_attested: bool | None,
    actor_login: str | None,
) -> Attestation | None:
    """同じ (指紋, hash) の確定が無いときだけ attested で記録し、監査に残す（`attest` と同じ監査）。

    既にあれば（並行して保存された `ack` も含め）何も変えず None。判定と書込みを 1 文で行う。
    """
    saved = await AttestationRepository(session).insert_if_absent(
        workspace=workspace,
        project_id=project_id,
        fingerprint=fingerprint,
        content_hash=content_hash,
        actor=actor,
        self_attested=self_attested,
    )
    if saved is not None:
        await _audit_attestation(session, saved, actor, via, self_attested, actor_login)
    return saved


async def _audit_attestation(
    session: AsyncSession,
    saved: Attestation,
    first_actor: str,
    via: str,
    self_attested: bool | None,
    actor_login: str | None,
) -> None:
    detail: dict[str, object] = {
        "content_hash": saved.content_hash,
        "project_id": saved.project_id,
        "via": via,
        # この確定の照合結果と、保存した印（true > null > false で強い方を保つ）
        "self_attested": self_attested,
        "self_attested_label": self_attest_label(self_attested),
        "self_attested_stored": saved.self_attested,
        "first_actor": first_actor,
        "latest_actor": saved.actor,
    }
    if actor_login is not None:
        detail["actor_login"] = actor_login
    warning = config_change_warning(saved.fingerprint, self_attested)
    if warning is not None:
        detail["warning"] = warning
    await AuditRepository(session).record(
        saved.actor,
        "attestation.record",
        f"{saved.workspace}:{saved.fingerprint}",
        saved.disposition,
        detail,
    )


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


BINDINGS_VIA_PR_COMMENT_REASON = (
    "対応表・免除・規約の変更の確定は PR の Approve か PR コメントで行う"
)


class BindingsAttestRequiresPrComment(PermissionError):
    """KNOWLEDGE-BINDINGS-001 の確定を REST で行おうとした（裁定 35。PR コメントだけ受け付ける）。

    画面の uid は GitHub のアカウントと対応しておらず PR の作成者と照合できない。判定の前提を
    変える確定は、作成者との照合ができる経路に限る。
    """


# SpecLino の uid → 連携済みの GitHub ユーザー ID（未連携なら None）
GithubIdOf = Callable[[str], Awaitable[int | None]]


class AttestTargetNotFound(LookupError):
    """確定の対象が見つからない（run が無い・他のワークスペース・その run に無い指摘）。404。"""


NOT_IN_RUN_REASON = "この run の検査に無い指紋"


async def attest_as_member(
    session: AsyncSession,
    *,
    workspace: str,
    uid: str,
    fingerprint: str,
    content_hash: str,
    disposition: Disposition,
    run_id: uuid.UUID,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
    github_id_of: GithubIdOf | None = None,
) -> tuple[Attestation, tuple[Project, ReviewRun]]:
    """画面（REST）からの確定。owner / approver だけ。戻り値は (確定, 再検査する (project, run))。

    `run_id`（確定した画面の run）は必須（#114）。run がこのワークスペースのプロジェクトのもので、
    (指紋, hash) がその run の保存済みの Findings の確定できる指摘に一致するときだけ受け付け、確定は
    その run のプロジェクトに属する。どちらでもなければ `AttestTargetNotFound`（404、存在を漏らさ
    ない）。再検査はその PR の最新の run の head で行う。
    自己確定は GitHub のユーザー ID で照合する: 確定者の uid → 連携済みの GitHub ID
    （`github_id_of`）と、run の PR の作成者の ID（`pr_author_id`）。どちらかが無い（未連携・
    作成者の ID の無い run）なら None（照合不可）。

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
    else:
        found = await _run_in_workspace(session, workspace, run_id)
        if found is None:
            reject = AttestTargetNotFound(f"run {run_id} が無い")
        elif not await in_run_findings(
            session, run_id=run_id, fingerprint=fingerprint, content_hash=content_hash
        ):
            reject = AttestTargetNotFound(NOT_IN_RUN_REASON)
    if reject is not None:
        if audit_factory is not None:
            async with audit_factory() as s, s.begin():
                await AuditRepository(s).record(
                    uid,
                    "attestation.record",
                    f"{workspace}:{fingerprint}",
                    "rejected",
                    {
                        "content_hash": content_hash,
                        "via": "api",
                        "run_id": str(run_id),
                        "reason": str(reject),
                    },
                )
        raise reject
    target = await recheck_target(session, workspace, run_id)
    project, _ = target
    saved = await attest(
        session,
        fingerprint=fingerprint,
        content_hash=content_hash,
        disposition=disposition,
        actor=uid,
        workspace=workspace,
        project_id=project.id,
        via="api",
        self_attested=await _self_attested(uid, target, github_id_of),
    )
    return saved, target


async def _run_in_workspace(
    session: AsyncSession, workspace: str, run_id: uuid.UUID
) -> tuple[Project, ReviewRun] | None:
    run = await ReviewRunRepository(session).get(run_id)
    project = await ProjectRepository(session).get(run.project_id) if run is not None else None
    if run is None or project is None or project.team != workspace:
        return None
    return project, run


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
    found = await _run_in_workspace(session, workspace, run_id)
    if found is None:
        raise LookupError(f"run {run_id} が無い")
    project, run = found
    runs = ReviewRunRepository(session)
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
    run = await ReviewRunRepository(session).latest_completed_for_pr(project_id, pr_number)
    if run is None:
        return False
    return await in_run_findings(
        session, run_id=run.id, fingerprint=fingerprint, content_hash=content_hash
    )


async def in_run_findings(
    session: AsyncSession, *, run_id: uuid.UUID, fingerprint: str, content_hash: str
) -> bool:
    """(指紋, hash) がその run の保存済みの Findings の確定できる指摘にあるか。"""
    from speclino.db.repositories import FindingRepository

    for row in await FindingRepository(session).list_for_run(run_id):
        if attestation_target(Finding.model_validate(row.payload)) == (fingerprint, content_hash):
            return True
    return False


BULK_NOT_REGISTERED_REASON = "ワークスペースに登録されていないリポジトリ"
BULK_NOT_APPROVER_REASON = "承認者ではない"
BULK_STALE_REASON = "Approve したコミットが PR の最新の検査の head と違う"
BULK_PENDING_REASON = "PR の最新のコミットの検査がまだ完了していない（完了してからやり直す）"
BULK_NOTHING_REASON = "確定できる要確認が無い（確定済みを含む）"
BULK_REAPPLY_REASON = "確定済みの要確認が検査に反映されていないため再検査する"

# 一括の確定の経路: PR の Approve と、引数なしの PR コメント `/speclino attest`
BulkSource = Literal["pr_review", "pr_comment_all"]


@dataclass(frozen=True)
class BulkAttest:
    """PR の最新の検査の要確認をまとめて確定する依頼（Approve か引数なしの `/speclino attest`）。

    `commit_id` は Approve したコミット。コメントには無いので None（最新の run の head を使う）。
    """

    source: BulkSource
    pr_number: int
    commit_id: str | None
    actor_login: str
    actor_id: int | None
    url: str
    pr_author_id: int | None


@dataclass(frozen=True)
class BulkAttestOutcome:
    """一括の確定の結果。`recheck` は再検査する run（確定が 1 件以上のときだけ）。"""

    accepted: bool
    reason: str
    attested: list[Attestation] = field(default_factory=list)
    recheck: ReviewRun | None = None


async def attest_all_in_pr(
    session: AsyncSession, *, project: Project, req: BulkAttest
) -> BulkAttestOutcome:
    """承認者の依頼で、その PR の最新の検査の確定できる要確認すべてを attested で確定する。

    PR コメントの `/speclino attest <指紋> <hash>` と同じ規則で記録する: 承認者は
    `projects.settings` の `approver_ids` と GitHub のユーザー ID で照合し（`is_approver`）、
    actor は `github:<ID>`、自己確定の印・対応表の変更の警告は `attest` が残す。経路は監査の
    `via`（`<source>:<URL>`）で区別する。指紋と内容の hash は利用者が打たず、run の保存済みの
    Findings の値を使う。

    対象はその PR の最新の run で、最新の完了した run と head が一致し（検査中・未検査のコミットが
    無い）、Approve ならその head が Approve したコミットと一致するものだけ（古いコミットへの承認で
    新しい変更を確定しない）。確定できるのは `attestation_target` が返す REVIEW だけで、VIOLATED
    などは対象外。既に確定がある (指紋, hash) は上書きしない（重複の依頼は冪等。`ack` で問題として
    記録したものを覆さない）。結果は受理・拒否とも監査（`attestation.bulk`）に残す。
    """
    actor = f"github:{req.actor_id}" if req.actor_id is not None else req.actor_login
    outcome = await _attest_all(session, project, req, actor)
    await AuditRepository(session).record(
        actor,
        "attestation.bulk",
        f"{project.repo_full_name}#{req.pr_number}",
        _bulk_decision(outcome),
        {
            "source": req.source,
            "actor_login": req.actor_login,
            "commit_id": req.commit_id,
            "url": req.url,
            "reason": outcome.reason,
            "attested": [a.fingerprint for a in outcome.attested],
        },
    )
    return outcome


def _bulk_decision(outcome: BulkAttestOutcome) -> str:
    if outcome.accepted:
        return "accepted"
    return "ignored" if outcome.reason == BULK_NOTHING_REASON else "rejected"


async def _attest_all(
    session: AsyncSession, project: Project, req: BulkAttest, actor: str
) -> BulkAttestOutcome:
    if project.team is None:
        return BulkAttestOutcome(False, BULK_NOT_REGISTERED_REASON)
    if not is_approver(project.settings, user_id=req.actor_id, login=req.actor_login):
        return BulkAttestOutcome(False, f"{req.actor_login} は{BULK_NOT_APPROVER_REASON}")
    runs = ReviewRunRepository(session)
    latest = await runs.latest_run_for_pr(project.id, req.pr_number)
    if latest is None or (req.commit_id is not None and latest.head_sha != req.commit_id):
        return BulkAttestOutcome(False, BULK_STALE_REASON)
    done = await runs.latest_completed_for_pr(project.id, req.pr_number)
    if done is None or done.head_sha != latest.head_sha:
        return BulkAttestOutcome(False, BULK_PENDING_REASON)
    author_id = req.pr_author_id if req.pr_author_id is not None else done.pr_author_id
    targets = sorted(await _run_targets(session, done.id))
    saved = [
        a
        for fp, ch in targets
        if (
            a := await attest_if_absent(
                session,
                fingerprint=fp,
                content_hash=ch,
                actor=actor,
                actor_login=req.actor_login,
                workspace=project.team,
                project_id=project.id,
                via=f"{req.source}:{req.url}",
                self_attested=same_github_user(req.actor_id, author_id),
            )
        )
        is not None
    ]
    if saved:
        return BulkAttestOutcome(True, "承認者の一括の確定", saved, latest)
    # 新しい確定が無くても、確定済みの attested がまだ検査に反映されていない（前の再検査の投入に
    # 失敗した・再送された）なら再検査し直す。未完了の run は create_run_in が superseded にする
    if await _unapplied_attestations(session, project.team, project.id, done, targets):
        return BulkAttestOutcome(True, BULK_REAPPLY_REASON, [], latest)
    return BulkAttestOutcome(False, BULK_NOTHING_REASON)


async def _unapplied_attestations(
    session: AsyncSession,
    workspace: str,
    project_id: uuid.UUID,
    done: ReviewRun,
    targets: list[tuple[str, str]],
) -> bool:
    """`done` の要確認に attested の確定があるのに、`done` の検査に効いていないか。"""
    attested = {
        (a.fingerprint, a.content_hash)
        for a in await list_attestations(session, workspace, project_id)
        if a.disposition == "attested"
    }
    applied = {
        (a.get("fingerprint"), a.get("content_hash"))
        for a in (done.manifest or {}).get("attestations", [])
        if isinstance(a, dict)
    }
    return any(t in attested and t not in applied for t in targets)


async def _run_targets(session: AsyncSession, run_id: uuid.UUID) -> set[tuple[str, str]]:
    from speclino.db.repositories import FindingRepository

    rows = await FindingRepository(session).list_for_run(run_id)
    return {
        t
        for row in rows
        if (t := attestation_target(Finding.model_validate(row.payload))) is not None
    }


async def list_attestations(
    session: AsyncSession, workspace: str, project_id: uuid.UUID
) -> list[Attestation]:
    """そのプロジェクトの確定（run のゲートに効かせるもの）。"""
    return await AttestationRepository(session).list_for_project(workspace, project_id)


__all__ = [
    "CONFIG_CHANGE_REQUIREMENT_ID",
    "CONTENT_HASH_REF",
    "FINGERPRINT_REF",
    "NOT_IN_PR_REASON",
    "NOT_IN_RUN_REASON",
    "AttestTargetNotFound",
    "BULK_NOTHING_REASON",
    "BULK_PENDING_REASON",
    "BULK_STALE_REASON",
    "BulkAttest",
    "BulkAttestOutcome",
    "BulkSource",
    "attest_all_in_pr",
    "attest_if_absent",
    "in_run_findings",
    "applied_attestations",
    "apply_attestations",
    "attest",
    "attest_as_member",
    "in_pr_findings",
    "attestation_target",
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
