"""可観測性の集計（S71、設計 9 章）。知識が使われ、守られているかを 1 つの集計にまとめる。

`workspace_health` が画面と REST の唯一の集計で、画面は数え直さない。測っていないものは None
（「未計測」）にし、0% や 100% にしない。クエリの数はプロジェクトの数に比例しない（N+1 にしない）。
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import (
    ArtifactRecord,
    ArtifactRevisionRecord,
    AttestationRecord,
    FindingRecord,
    KnowledgeFetchRecord,
    LlmAssessmentRecord,
    McpTokenRecord,
    Project,
    ReviewRun,
    SyncTokenRecord,
    WorkspaceMember,
)
from speclino.db.repositories.bundles import BundleRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.finding import Finding
from speclino.domain.knowledge import Bundle
from speclino.domain.observability import (
    FetchHealth,
    LlmStability,
    LlmUsageStats,
    ProjectHealth,
    Ratio,
    RulePoint,
    RuleTier,
    RuleTrend,
    RunSummary,
    TrendRun,
    UnknownBreakdown,
    UnknownReason,
    WorkspaceHealth,
)
from speclino.domain.policy import PolicyRule
from speclino.domain.uml import RequirementRow
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID
from speclino.services.project_overview import count_verdicts, policy_rule_id

# 検査器（conformance/・services/check_pipeline.py）が UNKNOWN に付ける決まった言い回しの接頭辞。
# Finding に理由の欄は無い（契約を変えない）ので、言い回しで分類する。判別できなければ other。
# 言い回しは test_observability_reasons.py が検査器のソースと突き合わせて固定する。
_UNBOUND = re.compile(
    r"^図の要素に対応する操作が見つからない: "
    r"|^状態の属性 \S+ に対応する列挙型の変数が見つからない$"
    r"|^図の状態 .+ が列挙 \S+ に無い（対応が見つからない）$"
    r"|^図の遷移を行うコードが対応に無い（"
    r"|^入口の操作に解析経路が無い$"
    r"|^解析経路が無い$"
)
_LIMIT = re.compile(
    r"^網羅性の判定がソルバー上限に達した$|^図の模擬が上限に達した（|の判定がソルバー上限（\d+ms）に達した$"
)
_INEXACT = re.compile(
    r"^経路 \S+ (は解析の精度が |は不完全なため|の未解決要因が|のイベント抽出が不完全なため"
    r"|の呼出し .+ が条件付き（may）|の \S+ への代入が条件付き（may）)"
    r"|^振る舞い全体の未解決要因が"
    r"|入力の一部がどの解析経路にも対応しない（経路の列挙が不完全）$"
)


def unknown_reason(f: Finding) -> UnknownReason:
    """UNKNOWN の Finding の理由の区分。"""
    if f.category == "missing_finding":
        return "missing_finding"
    if f.requirement_id == LOCK_REQUIREMENT_ID:
        return "lock"
    if f.category == "solver_limit" or _LIMIT.search(f.message):
        return "analysis_limit"
    if _UNBOUND.search(f.message):
        return "unbound"
    if _INEXACT.search(f.message):
        return "inexact_path"
    return "other"


def rule_tier(rule: PolicyRule) -> RuleTier:
    """規約の段。段は PolicyRule.tier（S70）が正本で、検査器の無いルールだけ unverified にする。"""
    if rule.verifier is None:
        return "unverified"
    return rule.tier


def ratio(numerator: int, denominator: int) -> Ratio | None:
    """分母が 0 なら None（未計測）。"""
    if denominator == 0:
        return None
    return Ratio(numerator=numerator, denominator=denominator, rate=numerator / denominator)


# ---- 集計 -----------------------------------------------------------------------------------

FETCH_WINDOW_DAYS = 30
TREND_RUNS = 10
_DONE_STATES = ("completed", "awaiting_answer")

# 供給元は (session, ワークスペース, 推移の run（古い順）) を受け取る。
# 当たり率は同じ run の窓で数える
LlmStatsSource = Callable[[AsyncSession, str, list[ReviewRun]], Awaitable[LlmUsageStats | None]]


def _canonical(project: str) -> str | None:
    try:
        return str(uuid.UUID(project))
    except ValueError:
        return None


async def _spec_requirements(
    session: AsyncSession, workspace: str, bundle: Bundle | None
) -> dict[str, dict[tuple[uuid.UUID, str], RequirementRow]]:
    """今の bundle の spec の要求の表。project ID → (spec artifact, 要求 ID) → 行（重複なし）。

    spec のスコープは projects だけ（workspace スコープは skill / instructions の契約）。
    """
    members = bundle.members if bundle is not None else []
    keys = [(uuid.UUID(m.artifact_id), m.revision) for m in members if m.kind == "spec"]
    by_project: dict[str, dict[tuple[uuid.UUID, str], RequirementRow]] = defaultdict(dict)
    if not keys:
        return by_project
    stmt = (
        select(ArtifactRevisionRecord.artifact_id, ArtifactRevisionRecord.content)
        .join(ArtifactRecord, ArtifactRecord.id == ArtifactRevisionRecord.artifact_id)
        .where(
            ArtifactRecord.workspace == workspace,
            ArtifactRecord.kind == "spec",
            tuple_(ArtifactRevisionRecord.artifact_id, ArtifactRevisionRecord.revision).in_(keys),
        )
    )
    for artifact_id, content in (await session.execute(stmt)).tuples():
        compiled = content.get("compiled") or {}
        rows = [RequirementRow.model_validate(r) for r in compiled.get("requirements", [])]
        scope = content.get("scope") or {}
        for p in scope.get("projects", []):
            if (pid := _canonical(p)) is not None:
                for row in rows:
                    by_project[pid].setdefault((artifact_id, row.id), row)
    return by_project


async def _latest_runs(session: AsyncSession, pids: list[uuid.UUID]) -> list[ReviewRun]:
    """プロジェクトごとの最新の完了した run（`DISTINCT ON` の 1 クエリ）。"""
    if not pids:
        return []
    stmt = (
        select(ReviewRun)
        .where(ReviewRun.project_id.in_(pids), ReviewRun.exec_state.in_(_DONE_STATES))
        .order_by(ReviewRun.project_id, ReviewRun.created_at.desc(), ReviewRun.id)
        .distinct(ReviewRun.project_id)
    )
    return list((await session.execute(stmt)).scalars())


async def _trend_runs(session: AsyncSession, pids: list[uuid.UUID]) -> list[ReviewRun]:
    """ワークスペースの直近の完了した run（古い順）。"""
    if not pids:
        return []
    stmt = (
        select(ReviewRun)
        .where(ReviewRun.project_id.in_(pids), ReviewRun.exec_state.in_(_DONE_STATES))
        .order_by(ReviewRun.created_at.desc(), ReviewRun.id)
        .limit(TREND_RUNS)
    )
    return list(reversed((await session.execute(stmt)).scalars().all()))


async def _findings(
    session: AsyncSession, run_ids: list[uuid.UUID], *, policy_only: bool = False
) -> dict[uuid.UUID, list[Finding]]:
    out: dict[uuid.UUID, list[Finding]] = defaultdict(list)
    if not run_ids:
        return out
    stmt = select(FindingRecord.run_id, FindingRecord.payload).where(
        FindingRecord.run_id.in_(run_ids)
    )
    if policy_only:
        stmt = stmt.where(FindingRecord.behavior_id.like("policy:%"))
    for run_id, payload in await session.execute(stmt):
        out[run_id].append(Finding.model_validate(payload))
    return out


def _manifest_generation(run: ReviewRun) -> int | None:
    value = (run.manifest or {}).get("bundle_generation")
    return value if isinstance(value, int) else None


def _is_obligation(f: Finding) -> bool:
    """仕様側の義務の Finding（規約・知識の配信の Finding を除く）。"""
    return not (f.behavior_id.startswith("policy:") or f.behavior_id.startswith("knowledge:"))


def _binding(findings: list[Finding]) -> Ratio | None:
    obligations: set[str] = set()
    unbound: set[str] = set()
    for f in findings:
        if not _is_obligation(f):
            continue
        obligations.add(f.requirement_id)
        if f.verdict == "UNKNOWN" and unknown_reason(f) == "unbound":
            unbound.add(f.requirement_id)
    return ratio(len(obligations) - len(unbound), len(obligations))


def _breakdown(findings: list[Finding]) -> UnknownBreakdown:
    counter = Counter(unknown_reason(f) for f in findings if f.verdict == "UNKNOWN")
    return UnknownBreakdown.model_validate(dict(counter))


def _run_summary(run: ReviewRun, findings: list[Finding], current: int | None) -> RunSummary:
    generation = _manifest_generation(run)
    return RunSummary(
        run_id=str(run.id),
        pr_number=run.pr_number,
        head_sha=run.head_sha,
        created_at=run.created_at,
        gate_status=run.gate_status,
        counts=count_verdicts(f.verdict for f in findings),
        bundle_generation=generation,
        generation_lag=current - generation
        if current is not None and generation is not None
        else None,
    )


def _project_health(
    project: Project,
    rows: list[RequirementRow],
    run: ReviewRun | None,
    findings: list[Finding],
    current: int | None,
) -> ProjectHealth:
    formalized = sum(1 for r in rows if r.formalized_by)
    return ProjectHealth(
        project_id=str(project.id),
        repo_full_name=project.repo_full_name,
        formalization=ratio(formalized, len(rows)),
        binding=_binding(findings) if run is not None else None,
        unknown_breakdown=_breakdown(findings) if run is not None else None,
        latest_run=_run_summary(run, findings, current) if run is not None else None,
    )


async def _fetches(
    session: AsyncSession,
    workspace: str,
    since: datetime,
    current: int | None,
    repos: dict[uuid.UUID, str],
) -> list[FetchHealth]:
    """トークン（無ければメンバー）× プロジェクトごとの期間内の最後の取得と、未取得のトークン。"""
    key = (
        KnowledgeFetchRecord.channel,
        KnowledgeFetchRecord.token_id,
        KnowledgeFetchRecord.uid,
        KnowledgeFetchRecord.project_id,
    )
    stmt = (
        select(KnowledgeFetchRecord)
        .where(
            KnowledgeFetchRecord.workspace == workspace, KnowledgeFetchRecord.created_at >= since
        )
        .order_by(*key, KnowledgeFetchRecord.created_at.desc())
        .distinct(*key)
    )
    latest = list((await session.execute(stmt)).scalars())
    fetched = {row.token_id for row in latest if row.token_id is not None}
    # 失効していないトークン（未取得の行を出す）と、期間内に取得のあったトークン（名前と失効）だけ
    sync_stmt = select(SyncTokenRecord).where(
        SyncTokenRecord.team == workspace,
        or_(SyncTokenRecord.revoked_at.is_(None), SyncTokenRecord.id.in_(fetched)),
    )
    sync_tokens = list((await session.execute(sync_stmt)).scalars())
    mcp_stmt = select(McpTokenRecord.id, McpTokenRecord.name, McpTokenRecord.revoked_at).where(
        McpTokenRecord.team == workspace, McpTokenRecord.id.in_(fetched)
    )
    tokens: dict[uuid.UUID, tuple[str, bool]] = {
        t.id: (t.name, t.revoked_at is not None) for t in sync_tokens
    }
    for tid, name, revoked_at in (await session.execute(mcp_stmt)).tuples():
        tokens[tid] = (name, revoked_at is not None)
    member_stmt = select(WorkspaceMember.uid, WorkspaceMember.email).where(
        WorkspaceMember.team == workspace
    )
    emails = {uid: email for uid, email in (await session.execute(member_stmt)).tuples()}

    out: list[FetchHealth] = []
    for row in latest:
        token = tokens.get(row.token_id) if row.token_id is not None else None
        token_name = token[0] if token is not None else None
        revoked = token is not None and token[1]
        out.append(
            FetchHealth(
                channel=row.channel,
                token_id=str(row.token_id) if row.token_id is not None else None,
                token_name=token_name,
                revoked=revoked,
                uid=row.uid,
                member_email=emails.get(row.uid) if row.uid is not None else None,
                project_id=str(row.project_id) if row.project_id is not None else None,
                repo_full_name=repos.get(row.project_id) if row.project_id is not None else None,
                last_fetched_at=row.created_at,
                last_generation=row.bundle_generation,
                # 失効したトークンはもう取得できないので、遅れに数えない
                behind_by=current - row.bundle_generation
                if current is not None and not revoked
                else None,
            )
        )
    for t in sync_tokens:
        if t.revoked_at is not None or t.id in fetched:
            continue
        out.append(
            FetchHealth(
                channel="sync",
                token_id=str(t.id),
                token_name=t.name,
                revoked=False,
                uid=None,
                member_email=None,
                project_id=str(t.project_id),
                repo_full_name=repos.get(t.project_id),
                last_fetched_at=None,
                last_generation=None,
                behind_by=None,
            )
        )
    out.sort(key=lambda f: (f.repo_full_name or "", f.channel, f.token_name or f.uid or ""))
    return out


async def _policy_rules(
    session: AsyncSession, workspace: str, bundle: Bundle | None
) -> list[PolicyRule]:
    """bundle の規約の改訂（bundle が無ければ有効な改訂）のルール。"""
    repo = PolicyRevisionRepository(session)
    if bundle is not None and bundle.policy_revision is not None:
        revision = await repo.revision_at(workspace, bundle.policy_revision)
    else:
        revision = await repo.active_revision(workspace)
    return list(revision.package.rules) if revision is not None else []


def _rule_trends(
    rules: list[PolicyRule], runs: list[ReviewRun], findings: dict[uuid.UUID, list[Finding]]
) -> list[RuleTrend]:
    counts: dict[str, dict[uuid.UUID, Counter[str]]] = defaultdict(dict)
    for run in runs:
        for f in findings.get(run.id, []):
            counts[policy_rule_id(f)].setdefault(run.id, Counter())[f.verdict] += 1
    by_rule = {
        rule_id: {
            run_id: RulePoint(
                violated=c["VIOLATED"],
                review=c["REVIEW"],
                unknown=c["UNKNOWN"],
                verified=c["VERIFIED_IN_SCOPE"],
            )
            for run_id, c in per_run.items()
        }
        for rule_id, per_run in counts.items()
    }
    meta = {r.id: r for r in rules}
    out: list[RuleTrend] = []
    for rule_id in sorted({*meta, *by_rule}):
        rule = meta.get(rule_id)
        points = by_rule.get(rule_id, {})
        out.append(
            RuleTrend(
                rule_id=rule_id,
                title=rule.title if rule else None,
                level=rule.level if rule else None,
                tier=rule_tier(rule) if rule else None,
                points=[points.get(run.id) for run in runs],
            )
        )
    return out


def _llm(
    findings: dict[uuid.UUID, list[Finding]], stats: LlmUsageStats | None
) -> LlmStability | None:
    outcomes = Counter(
        f.assessment_outcome
        for fs in findings.values()
        for f in fs
        if f.assessment_outcome is not None
    )
    measured = stats is not None and (stats.cache_hit_rate is not None or bool(stats.attestations))
    if not outcomes and not measured:
        return None
    return LlmStability(
        issue=outcomes.get("issue", 0),
        no_issue=outcomes.get("no_issue", 0),
        split=outcomes.get("split", 0),
        cache_hit_rate=stats.cache_hit_rate if stats else None,
        attestations=stats.attestations if stats else None,
        self_attested=stats.self_attested if stats else None,
        attestations_unverified=stats.attestations_unverified if stats else None,
    )


async def llm_usage_stats(
    session: AsyncSession, workspace: str, runs: list[ReviewRun]
) -> LlmUsageStats:
    """LLM 支援の記録（S70 の `llm_assessments` / `attestations`）からの値。

    キャッシュの当たり率: 推移の run（直近 10 の完了した run）の manifest の
    `llm_assessment_keys` を 1 run・1 キーずつ数え、そのキーの記録（同じワークスペース）が
    **run の作成より前**からあったものを当たり（LLM を呼ばずに記録を使った）、run の作成以後に
    できたものを外れ（その run が評価した）とする。並行する run が先に評価した記録を使った場合も
    外れに数えるので、当たり率は低めに出る側に倒れる。記録が見つからないキーは判別できないので
    分母から外す。数えるものが無ければ None（未計測）。

    確定の件数: ワークスペースの今の確定（同じ指紋・内容の確定は 1 件に上書きされる）の件数と、
    そのうち自己確定（PR の作成者が自分で確定）・照合できない（self_attested が NULL）の件数。
    run の窓ではなく現在の状態。
    """
    uses = [
        (run.created_at, key)
        for run in runs
        for key in set((run.manifest or {}).get("llm_assessment_keys") or [])
    ]
    created: dict[str, datetime] = {}
    if uses:
        stmt = select(LlmAssessmentRecord.key, LlmAssessmentRecord.created_at).where(
            LlmAssessmentRecord.workspace == workspace,
            LlmAssessmentRecord.key.in_({k for _, k in uses}),
        )
        created = dict((await session.execute(stmt)).tuples().all())
    known = [(at, created[key]) for at, key in uses if key in created]
    hits = sum(1 for run_at, record_at in known if record_at < run_at)
    rate = ratio(hits, len(known))

    total, self_attested, unverified = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AttestationRecord.self_attested.is_(True)),
                func.count().filter(AttestationRecord.self_attested.is_(None)),
            ).where(AttestationRecord.workspace == workspace)
        )
    ).one()
    return LlmUsageStats(
        cache_hit_rate=rate.rate if rate is not None else None,
        attestations=total,
        self_attested=self_attested,
        attestations_unverified=unverified,
    )


async def workspace_health(
    session: AsyncSession,
    workspace: str,
    *,
    now: datetime,
    llm_stats: LlmStatsSource | None = None,
) -> WorkspaceHealth:
    """ワークスペースの可観測性の集計（画面と REST の唯一の集計）。

    どのクエリもワークスペースで絞る（他のワークスペースのデータを数えない）。メンバーかどうかの
    確認は呼び出し側（API）が行う。`llm_stats` は LLM 支援の記録（S70）の供給元（REST は
    `llm_usage_stats` を渡す）で、無ければキャッシュの当たり率と確定の件数は未計測。
    """
    projects = list(
        (
            await session.execute(
                select(Project).where(Project.team == workspace).order_by(Project.repo_full_name)
            )
        ).scalars()
    )
    pids = [p.id for p in projects]
    repos = {p.id: p.repo_full_name for p in projects}
    bundle = await BundleRepository(session).latest(workspace)
    current = bundle.generation if bundle is not None else None

    by_project = await _spec_requirements(session, workspace, bundle)
    latest = {r.project_id: r for r in await _latest_runs(session, pids)}
    latest_findings = await _findings(session, [r.id for r in latest.values()])
    project_health = [
        _project_health(
            p,
            list(by_project.get(str(p.id), {}).values()),
            latest.get(p.id),
            latest_findings.get(latest[p.id].id, []) if p.id in latest else [],
            current,
        )
        for p in projects
    ]

    trend = await _trend_runs(session, pids)
    trend_findings = await _findings(session, [r.id for r in trend], policy_only=True)
    rules = await _policy_rules(session, workspace, bundle)
    stats = await llm_stats(session, workspace, trend) if llm_stats is not None else None

    return WorkspaceHealth(
        workspace=workspace,
        generated_at=now,
        current_generation=current,
        fetch_window_days=FETCH_WINDOW_DAYS,
        projects=project_health,
        fetches=await _fetches(
            session, workspace, now - timedelta(days=FETCH_WINDOW_DAYS), current, repos
        ),
        trend_runs=[
            TrendRun(run_id=str(r.id), repo_full_name=repos[r.project_id], created_at=r.created_at)
            for r in trend
        ],
        rules=_rule_trends(rules, trend, trend_findings),
        llm=_llm(trend_findings, stats),
    )
