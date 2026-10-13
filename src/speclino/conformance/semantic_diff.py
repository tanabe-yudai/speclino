"""semantic_diff: base / head の適合結果と Code IR から意味差分を作る。

比較するのは「base 仕様 + base コード」と「head 仕様 + head コード」それぞれの判定結果であり、
head コードを base 仕様に照合しない（正式な仕様変更を実装違反にしない）。実装の意味差分は
Code IR の経路を内容（guard / state_updates / events / outcome / precision）で照合する。
path_id と source_ref は除くので、行番号やローカル変数名だけの変更は差分にならない。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from speclino.conformance.reasons import worst
from speclino.domain.code_ir import CodeIR, Event, Path
from speclino.domain.expr import describe_expr
from speclino.domain.finding import BaseHeadStatus, Finding, Findings, Verdict
from speclino.domain.semantic_diff import (
    BehaviorDelta,
    FindingChange,
    FindingDelta,
    PathSummary,
    RuleChange,
    RuleDelta,
    SemanticDiff,
    SemanticDiffCounts,
)
from speclino.domain.spec_ir import Rule, SpecIR
from speclino.spec.spec_validator import rule_content_hash

_DECIDED: frozenset[Verdict] = frozenset({"VERIFIED_IN_SCOPE", "VIOLATED"})
_UNDECIDED: frozenset[Verdict] = frozenset({"UNKNOWN", "REVIEW"})

# FindingChange のうち Finding の 4 値に写せるものだけ（残りは None のままにする）
_BASE_HEAD_STATUS: dict[str, BaseHeadStatus] = {
    "new_violation": "new",
    "existing_violation": "existing",
    "resolved_violation": "resolved",
    "unchanged": "unchanged",
}


@dataclass(frozen=True)
class DiffSide:
    """比較する片側（base または head）の入力一式。"""

    commit: str | None
    specs: list[SpecIR]
    code_irs: list[CodeIR]
    findings: Findings


def diff_rules(base: list[Rule], head: list[Rule]) -> list[RuleDelta]:
    """Rule ID ごとに内容ハッシュを比べる（approval_ref は含めない）。"""
    base_by = {r.id: r for r in base}
    head_by = {r.id: r for r in head}
    out: list[RuleDelta] = []
    for rule_id in sorted(set(base_by) | set(head_by)):
        head_rule = head_by.get(rule_id)
        if head_rule is None:
            removed = base_by[rule_id]
            out.append(
                RuleDelta(rule_id=rule_id, behavior_id=removed.behavior_id, status="removed")
            )
            continue
        base_rule = base_by.get(rule_id)
        if base_rule is None:
            status: RuleChange = "added"
        elif rule_content_hash(base_rule) == rule_content_hash(head_rule):
            status = "unchanged"
        else:
            status = "changed"
        out.append(RuleDelta(rule_id=rule_id, behavior_id=head_rule.behavior_id, status=status))
    return out


def classify_finding(base: Verdict | None, head: Verdict | None) -> FindingChange:
    if head is None:
        return "removed"
    if head == "VIOLATED":
        return "existing_violation" if base == "VIOLATED" else "new_violation"
    if base is None:
        return "added"
    if base == head:
        return "unchanged"
    if base in _DECIDED and head in _UNDECIDED:
        return "became_undecidable"
    if head == "VERIFIED_IN_SCOPE":
        return "resolved_violation" if base == "VIOLATED" else "became_decidable"
    return "changed"


def path_content_hash(path: Path) -> str:
    """経路の内容ハッシュ。path_id と位置情報（source_ref）は含めない。"""
    data = path.model_dump(
        mode="json",
        exclude={
            "path_id": True,
            "source_refs": True,
            "state_updates": {"__all__": {"source_ref"}},
            "events": {"__all__": {"source_ref"}},
            "outcome": {"source_ref"},
            "unknown_causes": {"__all__": {"source_ref"}},
        },
    )
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def summarize_path(path: Path) -> PathSummary:
    return PathSummary(
        path_id=path.path_id,
        guard=describe_expr(path.guard),
        events=[_describe_event(e) for e in path.events],
        outcome=_describe_outcome(path),
        precision=path.precision,
    )


def diff_code_irs(base: list[CodeIR], head: list[CodeIR]) -> list[BehaviorDelta]:
    base_by = {c.behavior_id: c for c in base}
    head_by = {c.behavior_id: c for c in head}
    out: list[BehaviorDelta] = []
    for behavior_id in sorted(set(base_by) | set(head_by)):
        head_ir = head_by.get(behavior_id)
        if head_ir is None:
            paths = [summarize_path(p) for p in base_by[behavior_id].paths]
            out.append(
                BehaviorDelta(behavior_id=behavior_id, status="removed", removed_paths=paths)
            )
            continue
        base_ir = base_by.get(behavior_id)
        if base_ir is None:
            paths = [summarize_path(p) for p in head_ir.paths]
            out.append(BehaviorDelta(behavior_id=behavior_id, status="added", added_paths=paths))
            continue
        base_counts = Counter(path_content_hash(p) for p in base_ir.paths)
        head_counts = Counter(path_content_hash(p) for p in head_ir.paths)
        added = _select_by_hash(head_ir.paths, head_counts - base_counts)
        removed = _select_by_hash(base_ir.paths, base_counts - head_counts)
        out.append(
            BehaviorDelta(
                behavior_id=behavior_id,
                status="unchanged" if not added and not removed else "changed",
                added_paths=[summarize_path(p) for p in added],
                removed_paths=[summarize_path(p) for p in removed],
                unchanged_paths=sum((base_counts & head_counts).values()),
            )
        )
    return out


def semantic_diff(
    base: DiffSide | None, head: DiffSide, base_error: str | None = None
) -> SemanticDiff:
    """base が無い（初回 / 判定不能）ときは head だけから「added」として並べる。"""
    # base が無いときは空の片側として扱う（head の判定はすべて added になる）
    base_side = base if base is not None else DiffSide(None, [], [], Findings())
    base_verdicts = _verdicts(base_side.findings)
    head_verdicts = _verdicts(head.findings)
    deltas: list[FindingDelta] = []
    for key in sorted(set(head_verdicts) | set(base_verdicts)):
        bv, hv = base_verdicts.get(key), head_verdicts.get(key)
        deltas.append(
            FindingDelta(
                behavior_id=key[0],
                requirement_id=key[1],
                base_verdict=bv,
                head_verdict=hv,
                status=classify_finding(bv, hv),
            )
        )
    counts: Counter[str] = Counter(d.status for d in deltas)
    base_rules = [r for s in base_side.specs for r in s.rules]
    head_rules = [r for s in head.specs for r in s.rules]
    return SemanticDiff(
        base_commit=base_side.commit,
        head_commit=head.commit or "WORKTREE",
        base_available=base is not None,
        base_error=base_error,
        findings=deltas,
        rules=diff_rules(base_rules, head_rules),
        behaviors=diff_code_irs(base_side.code_irs, head.code_irs),
        counts=SemanticDiffCounts(**{k: counts[k] for k in SemanticDiffCounts.model_fields}),
    )


def annotate_findings(findings: Findings, diff: SemanticDiff) -> Findings:
    """head の Finding に base_head_status（new / existing / resolved / unchanged）を付ける。

    Finding 側の 4 値に無い遷移（判定不能化など）は None のままにし、詳細は SemanticDiff が持つ。
    """
    status_by = {(d.behavior_id, d.requirement_id): d.status for d in diff.findings}
    updated: list[Finding] = []
    for f in findings.findings:
        change = status_by.get((f.behavior_id, f.requirement_id), "")
        status = _BASE_HEAD_STATUS.get(change)
        updated.append(f.model_copy(update={"base_head_status": status}) if status else f)
    return findings.model_copy(update={"findings": updated})


# ---------------------------------------------------------------------- internals


def _verdicts(findings: Findings) -> dict[tuple[str, str], Verdict]:
    """(behavior_id, requirement_id) → verdict。同じ鍵が複数あれば最も悪いものを採る。"""
    grouped: dict[tuple[str, str], list[Verdict]] = {}
    for f in findings.findings:
        grouped.setdefault((f.behavior_id, f.requirement_id), []).append(f.verdict)
    return {k: worst(v) for k, v in grouped.items()}


def _select_by_hash(paths: list[Path], wanted: Counter[str]) -> list[Path]:
    remaining = Counter(wanted)
    out: list[Path] = []
    for p in paths:
        h = path_content_hash(p)
        if remaining[h] > 0:
            remaining[h] -= 1
            out.append(p)
    return out


def _describe_event(e: Event) -> str:
    args = ", ".join(f"{k}={describe_expr(v)}" for k, v in sorted(e.arguments.items()))
    head = f"{e.kind}:{e.operation_or_target}@{e.phase}"
    if e.kind == "write" and e.value is not None:
        head += f" = {describe_expr(e.value)}"
    if args:
        head += f"({args})"
    return head + (" [may]" if e.certainty == "may" else "")


def _describe_outcome(path: Path) -> str:
    o = path.outcome
    if o.kind == "http_response":
        return f"http {o.status}"
    if o.kind == "exception":
        return f"raise {o.exception_type or '?'}"
    if o.kind == "return":
        return "return" + (f" {describe_expr(o.value)}" if o.value is not None else "")
    return "unresolved"


__all__ = [
    "DiffSide",
    "annotate_findings",
    "classify_finding",
    "diff_code_irs",
    "diff_rules",
    "path_content_hash",
    "semantic_diff",
    "summarize_path",
]
