"""プロジェクト画面（S45）の組み立て。design/2026-09-30-ui-redesign.md 5.2 節。

最新の完了した検査の成果物（Spec IR・findings・gate・semantic diff）から要求と規約の一覧を作る。
判定はしない（検査器の findings を要求・規約ごとに並べ直すだけ）。
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, ReviewRun
from speclino.db.repositories.findings import FindingRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.domain.finding import Finding, Verdict, VerdictCounts
from speclino.domain.policy import PolicyPackage
from speclino.domain.project_overview import (
    ApprovalState,
    PolicyView,
    ProjectOverview,
    ProjectSummary,
    RequirementView,
    RunView,
)
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import Rule, SpecIR
from speclino.ports.blob_store import BlobStore
from speclino.services.workspaces import WorkspaceNotFound, require_member

# 重い順（web/src/services/labels.ts の VERDICT_ORDER と同じ）
VERDICT_SEVERITY: tuple[Verdict, ...] = ("VIOLATED", "REVIEW", "UNKNOWN", "VERIFIED_IN_SCOPE")


def is_policy_finding(f: Finding) -> bool:
    return f.behavior_id.startswith("policy:")


def policy_rule_id(f: Finding) -> str:
    """規約 finding の requirement_id は `<rule>:<path>`（PolicyRule.id はコロンを含まない）。"""
    return f.requirement_id.split(":", 1)[0]


def worst_verdict(verdicts: Iterable[Verdict]) -> Verdict | None:
    present = set(verdicts)
    return next((v for v in VERDICT_SEVERITY if v in present), None)


def requirement_verdicts(findings: Iterable[Finding]) -> dict[str, Verdict]:
    grouped: dict[str, list[Verdict]] = {}
    for f in findings:
        if not is_policy_finding(f):
            grouped.setdefault(f.requirement_id, []).append(f.verdict)
    return {rid: v for rid, vs in grouped.items() if (v := worst_verdict(vs)) is not None}


def count_verdicts(verdicts: Iterable[Verdict]) -> VerdictCounts:
    c = Counter(verdicts)
    return VerdictCounts(**{v: c.get(v, 0) for v in VERDICT_SEVERITY})


def _approval(rule: Rule, spec: SpecIR, changed: set[tuple[str, str]]) -> ApprovalState:
    # conformance/engine.py の _approved と同じ条件
    if rule.approval_ref is not None and any(a.id == rule.approval_ref for a in spec.approvals):
        return "approved"
    return "stale" if (spec.behavior_id, rule.id) in changed else "unapproved"


def build_requirements(
    spec_irs: list[SpecIR], findings: list[Finding], diff: SemanticDiff | None
) -> list[RequirementView]:
    verdicts = requirement_verdicts(findings)
    ids: dict[str, list[str]] = {}
    for f in findings:
        if not is_policy_finding(f):
            ids.setdefault(f.requirement_id, []).append(f.id)
    changed = {
        (d.behavior_id, d.rule_id) for d in (diff.rules if diff else []) if d.status == "changed"
    }

    rows: list[RequirementView] = []
    known: set[str] = set()
    for spec in spec_irs:
        for rule in spec.rules:
            known.add(rule.id)
            ref = next((r for r in rule.source_refs if r.path), None)
            rows.append(
                RequirementView(
                    id=rule.id,
                    behavior_id=spec.behavior_id,
                    title=rule.title,
                    spec_path=ref.path if ref else None,
                    line_start=ref.line_start if ref else None,
                    line_end=ref.line_end if ref else None,
                    approval=_approval(rule, spec, changed),
                    tests_declared=len(rule.tests),
                    verdict=verdicts.get(rule.id),
                    finding_ids=sorted(ids.get(rule.id, [])),
                )
            )
    # head の Spec IR から消えた要求（承認なしの削除は stale_approval の finding が残る）
    for f in findings:
        if is_policy_finding(f) or f.requirement_id in known:
            continue
        known.add(f.requirement_id)
        same = [
            g for g in findings if g.requirement_id == f.requirement_id and not is_policy_finding(g)
        ]
        ref = next((r for g in same for r in g.spec_refs if r.path), None)
        rows.append(
            RequirementView(
                id=f.requirement_id,
                behavior_id=f.behavior_id,
                title=None,
                spec_path=ref.path if ref else None,
                line_start=ref.line_start if ref else None,
                line_end=ref.line_end if ref else None,
                approval="stale"
                if any(g.category == "stale_approval" for g in same)
                else "approved",
                tests_declared=0,
                verdict=verdicts.get(f.requirement_id),
                finding_ids=sorted(g.id for g in same),
                removed=True,
            )
        )
    return rows


def build_policies(
    findings: list[Finding], gate: dict[str, Any] | None, team: PolicyPackage | None
) -> list[PolicyView]:
    blocking = set(gate.get("blocking_ids", [])) if gate else set()
    advisory = set(gate.get("advisory_ids", [])) if gate else set()
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        if is_policy_finding(f):
            grouped.setdefault(policy_rule_id(f), []).append(f)
    team_rules = {r.id: r for r in team.rules} if team else {}

    rows: list[PolicyView] = []
    for rid in [*team_rules, *sorted(k for k in grouped if k not in team_rules)]:
        fs = grouped.get(rid, [])
        rule = team_rules.get(rid)
        if rule is not None:
            rows.append(
                PolicyView(
                    rule_id=rid,
                    origin="team",
                    title=rule.title,
                    level=rule.level,
                    has_verifier=rule.verifier is not None,
                    status=worst_verdict(f.verdict for f in fs),
                    counts=count_verdicts(f.verdict for f in fs),
                )
            )
            continue
        fids = {f.id for f in fs}
        level = "mandatory" if fids & blocking else "advisory" if fids & advisory else None
        rows.append(
            PolicyView(
                rule_id=rid,
                origin="project",
                title=None,
                level=level,
                has_verifier=None,
                status=worst_verdict(f.verdict for f in fs),
                counts=count_verdicts(f.verdict for f in fs),
            )
        )
    return rows


HISTORY_LIMIT = 20


def run_view(run: ReviewRun) -> RunView:
    return RunView(
        id=str(run.id),
        pr_number=run.pr_number,
        head_sha=run.head_sha,
        exec_state=run.exec_state,
        gate_status=run.gate_status,
        created_at=run.created_at,
    )


async def _findings(session: AsyncSession, run_id: uuid.UUID) -> list[Finding]:
    return [
        Finding.model_validate(r.payload)
        for r in await FindingRepository(session).list_for_run(run_id)
    ]


async def _summary(session: AsyncSession, blobs: BlobStore, project: Project) -> ProjectSummary:
    latest = await ReviewRunRepository(session).latest_completed(project.id)
    counts: VerdictCounts | None = None
    unanalyzed: int | None = None
    if latest is not None:
        verdicts = requirement_verdicts(await _findings(session, latest.id))
        counts = count_verdicts(verdicts.values())
        raw = await _optional_json(blobs, latest.id, "findings.json")
        total = raw.get("coverage", {}).get("requirements_total") if isinstance(raw, dict) else None
        if isinstance(total, int):
            unanalyzed = max(0, total - len(verdicts))
    return ProjectSummary(
        id=str(project.id),
        repo_full_name=project.repo_full_name,
        default_branch=project.default_branch,
        latest_run=run_view(latest) if latest else None,
        counts=counts,
        unanalyzed=unanalyzed,
    )


async def list_projects(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str
) -> list[ProjectSummary]:
    await require_member(session, slug, uid)
    stmt = select(Project).where(Project.team == slug).order_by(Project.repo_full_name)
    return [await _summary(session, blobs, p) for p in (await session.execute(stmt)).scalars()]


async def _json(blobs: BlobStore, run_id: uuid.UUID, name: str) -> Any:
    return json.loads(await blobs.get(f"runs/{run_id}/{name}"))


class ArtifactsIncomplete(LookupError):
    """manifest にある behavior の Spec IR が揃っていない。"""


async def load_spec_irs(blobs: BlobStore, run_id: uuid.UUID, expected: list[str]) -> list[SpecIR]:
    """run の Spec IR を behavior ごとに読む（services/check_pipeline.py の write_result）。

    behavior が複数なら `spec-ir.<behavior>.json`、1 つなら `spec-ir.json`（複数のときも先頭の
    behavior の `spec-ir.json` が残るので、見つかったファイルからは数を決めない）。`expected` は
    manifest の `analysis[].behavior_id`。空（S41c より前の run）なら見つかったファイルに従う。
    """
    if not expected:
        names = [k.rsplit("/", 1)[-1] for k in await blobs.list(f"runs/{run_id}/")]
        per = sorted(
            n
            for n in names
            if n.startswith("spec-ir.") and n.endswith(".json") and n != "spec-ir.json"
        )
        targets = per or ["spec-ir.json"]
    elif len(expected) == 1:
        targets = ["spec-ir.json"]
    else:
        targets = [f"spec-ir.{b}.json" for b in expected]
    specs = [SpecIR.model_validate(await _json(blobs, run_id, n)) for n in targets]
    if expected and sorted(s.behavior_id for s in specs) != sorted(expected):
        raise ArtifactsIncomplete(str(run_id))
    return specs


async def _optional_json(blobs: BlobStore, run_id: uuid.UUID, name: str) -> Any | None:
    try:
        return await _json(blobs, run_id, name)
    except (FileNotFoundError, OSError):
        return None


async def load_overview(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str, project_id: uuid.UUID
) -> ProjectOverview:
    await require_member(session, slug, uid)
    project = await ProjectRepository(session).get(project_id)
    if project is None or project.team != slug:
        raise WorkspaceNotFound(slug)
    runs = ReviewRunRepository(session)
    summary = await _summary(session, blobs, project)
    history = [run_view(r) for r in await runs.list_for_project(project.id, HISTORY_LIMIT)]
    latest = await runs.latest_completed(project.id)
    if latest is None:
        return ProjectOverview(status="not_checked", project=summary, runs=history)
    manifest = latest.manifest or {}
    expected = [
        a["behavior_id"]
        for a in manifest.get("analysis", [])
        if isinstance(a, dict) and "behavior_id" in a
    ]
    try:
        spec_irs = await load_spec_irs(blobs, latest.id, expected)
        raw_diff = await _optional_json(blobs, latest.id, "semantic-diff.json")
        diff = SemanticDiff.model_validate(raw_diff) if raw_diff is not None else None
        gate = await _optional_json(blobs, latest.id, "gate.json")
    except (FileNotFoundError, OSError, ValueError, ValidationError, ArtifactsIncomplete):
        return ProjectOverview(
            status="unavailable", project=summary, run=run_view(latest), runs=history
        )

    team_name, revision = manifest.get("policy_team"), manifest.get("policy_revision")
    team = None
    if isinstance(team_name, str) and isinstance(revision, int):
        found = await PolicyRevisionRepository(session).revision_at(team_name, revision)
        team = found.package if found is not None else None

    findings = await _findings(session, latest.id)
    requirements = build_requirements(spec_irs, findings, diff)
    present = [r for r in requirements if not r.removed]
    return ProjectOverview(
        status="ok",
        project=summary,
        run=run_view(latest),
        counts=count_verdicts(r.verdict for r in present if r.verdict is not None),
        unanalyzed=sum(1 for r in present if r.verdict is None)
        + sum(len(s.unextracted) for s in spec_irs),
        unextracted=[u for s in spec_irs for u in s.unextracted],
        unapproved=sum(1 for r in requirements if r.approval != "approved"),
        policy_team=team_name if isinstance(team_name, str) else None,
        policy_revision=revision if isinstance(revision, int) else None,
        requirements=requirements,
        policies=build_policies(findings, gate, team),
        runs=history,
    )
