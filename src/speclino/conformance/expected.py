"""期待する Finding の列挙と欠落の補完（設計 5.2）。

run は bundle から「出るべき要求 ID」を列挙し、検査の後、一覧にあるのに Finding が無い要求に
UNKNOWN（`missing_finding`）を合成する。ゲートが「出てきた Finding だけ」で判定して、出なかった
必須の要求が素通りする穴を塞ぐ。
"""

from __future__ import annotations

from speclino.conformance.reasons import finding_id
from speclino.domain.finding import CoverageCounts, Finding, Findings, VerdictCounts
from speclino.domain.spec_ir import RuleLevel
from speclino.domain.uml import CompiledSpec


def obligation_levels(compiled: CompiledSpec) -> dict[str, RuleLevel]:
    """義務 ID（構造・順序/包含・遷移）と旧形式の要求 ID → level。"""
    levels: dict[str, RuleLevel] = {}
    for s in compiled.structure:
        levels[s.id] = s.level
    for t in compiled.traces:
        levels[t.id] = t.level
    for m in compiled.state_machines:
        levels[m.id] = m.level
    for spec in compiled.legacy:
        for r in spec.rules:
            levels[r.id] = r.level
    return levels


def expected_ids(compiled: CompiledSpec, policy_rule_ids: list[str]) -> list[str]:
    """出るべき要求 ID（義務・旧形式の要求・規約）。順序は決定的で重複なし。"""
    return list(dict.fromkeys([*obligation_levels(compiled), *policy_rule_ids]))


def _counts(findings: list[Finding]) -> VerdictCounts:
    counts = VerdictCounts()
    for f in findings:
        counts = counts.model_copy(update={f.verdict: getattr(counts, f.verdict) + 1})
    return counts


def fill_missing(findings: Findings, expected: list[str], behavior_id: str) -> Findings:
    """expected のうち Finding が 1 つも無い ID に UNKNOWN / missing_finding を足す。"""
    present = {f.requirement_id for f in findings.findings}
    added = [
        Finding(
            id=finding_id(rid, behavior_id, None, "missing_finding"),
            requirement_id=rid,
            behavior_id=behavior_id,
            verdict="UNKNOWN",
            category="missing_finding",
            severity="medium",
            message=(
                f"要求 {rid} の判定結果が出ていない（検査器の例外・図の要素の対応なし・"
                "LLM の失敗など）。成立とはみなさない"
            ),
            claim_scope="command_invocation",
            next_steps=["検査ログを確認し、対応（bindings）や検査器の失敗を解消して再実行する"],
        )
        for rid in dict.fromkeys(expected)
        if rid not in present
    ]
    if not added:
        return findings
    all_findings = [*findings.findings, *added]
    return findings.model_copy(update={"findings": all_findings, "counts": _counts(all_findings)})


def uml_coverage(
    compiled: CompiledSpec, findings: list[Finding], *, approved: bool
) -> CoverageCounts:
    """UML の仕様の要求の行を数える（旧形式の要求は engine が数えるので、`ir:` だけの行は除く）。

    - 要求数: 行の数
    - 承認済み: 承認済みの bundle の仕様なら行の数、手元の仕様なら 0
    - 対応範囲: 図の要素に結びついた（形式化された）行。このプロジェクトに当たる仕様だけが渡る
    - 判定済み: 対応範囲の行のうち、参照する図の義務がすべて適合か違反に決まったもの
    - 未解析: 形式化されておらず検査できない行
    """
    rows = [
        r
        for r in compiled.requirements
        if not (r.formalized_by and all(x.startswith("ir:") for x in r.formalized_by))
    ]
    obligation_ids = list(obligation_levels(compiled))
    in_scope = [r for r in rows if r.formalized_by]
    decided = 0
    for row in in_scope:
        diagrams = {x.split("#", 1)[0] for x in row.formalized_by if "#" in x}
        mine = [o for o in obligation_ids if o.split("#", 1)[0] in diagrams]
        if mine and all(_is_decided(o, findings) for o in mine):
            decided += 1
    return CoverageCounts(
        requirements_total=len(rows),
        requirements_approved=len(rows) if approved else 0,
        requirements_in_scope=len(in_scope),
        requirements_decided=decided,
        requirements_unanalyzed=len(rows) - len(in_scope),
    )


def _is_decided(obligation_id: str, findings: list[Finding]) -> bool:
    """義務の Finding（`<ID>` または `<ID>:<経路>`）があり、すべて適合か違反に決まっている。"""
    own = [
        f
        for f in findings
        if f.requirement_id == obligation_id or f.requirement_id.startswith(obligation_id + ":")
    ]
    return bool(own) and all(f.verdict in ("VERIFIED_IN_SCOPE", "VIOLATED") for f in own)


__all__ = ["expected_ids", "fill_missing", "obligation_levels", "uml_coverage"]
