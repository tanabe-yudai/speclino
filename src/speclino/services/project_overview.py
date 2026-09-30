"""プロジェクト画面（S45）の組み立て。design/2026-09-30-ui-redesign.md 5.2 節。

最新の完了した検査の成果物（Spec IR・findings・gate・semantic diff）から要求と規約の一覧を作る。
判定はしない（検査器の findings を要求・規約ごとに並べ直すだけ）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from speclino.domain.finding import Finding, Verdict, VerdictCounts
from speclino.domain.policy import PolicyPackage
from speclino.domain.project_overview import ApprovalState, PolicyView, RequirementView
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import Rule, SpecIR

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
