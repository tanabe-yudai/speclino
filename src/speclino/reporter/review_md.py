"""reporter.review_md（PoC 13 章「レポートの仕様」/ 17.3「一致率だけを出さない」）。

Findings と GateResult を人間向け Markdown（`review.md`）に圧縮する。GitHub Checks の
summary/text にも使うため見出しは `##` 以下、表は GFM で書く。
"""

from __future__ import annotations

from speclino.conformance.gate import GateResult
from speclino.domain.common import SourceRef
from speclino.domain.finding import Finding, Findings, TestObservation, Verdict
from speclino.domain.run import GateStatus, RunManifest

VERDICT_LABELS: dict[Verdict, str] = {
    "VERIFIED_IN_SCOPE": "適合（確認範囲内）",
    "VIOLATED": "違反",
    "UNKNOWN": "不明",
    "REVIEW": "要確認",
}

GATE_STATUS_LABELS: dict[GateStatus, str] = {
    "pass": "通過",
    "pass_with_exceptions": "例外付き通過",
    "fail": "失敗",
}

_TEST_OBSERVATION_LABELS: dict[TestObservation, str] = {
    "OBSERVED": "観測済み",
    "NOT_OBSERVED": "未観測",
    "CONTRADICTED": "矛盾",
}

# VIOLATED → REVIEW → UNKNOWN の順で詳細を出す（VERIFIED は表にまとめて別扱い）。
_DETAIL_ORDER: tuple[Verdict, ...] = ("VIOLATED", "REVIEW", "UNKNOWN")


def _scope_and_assumptions(findings: list[Finding]) -> tuple[list[str], list[str]]:
    """確認範囲 / 仮定は manifest から取れないため、Finding 群から集合として拾う。

    出現順を保ったまま重複を除く（dict.fromkeys はキー順を保持する）。
    """
    scopes = list(dict.fromkeys(f.analysis_scope for f in findings if f.analysis_scope))
    assumptions = list(dict.fromkeys(a for f in findings for a in f.assumptions))
    return scopes, assumptions


def _render_overview(
    manifest: RunManifest, findings: Findings, gate: GateResult | None, behavior_id: str
) -> str:
    counts = findings.counts
    scopes, assumptions = _scope_and_assumptions(findings.findings)
    base = manifest.inputs.base_commit or "(base なし)"
    lines = [
        f"- 対象: `{behavior_id}`",
        f"- 比較: `{base}` → `{manifest.inputs.head_commit}`",
        f"- 要求: {len(findings.findings)}件",
        "",
        "| 判定 | 件数 |",
        "| --- | --- |",
    ]
    for v in ("VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW"):
        lines.append(f"| {VERDICT_LABELS[v]} | {getattr(counts, v)} |")
    lines.append("")
    if gate is not None:
        lines.append(f"- ゲート: **{GATE_STATUS_LABELS[gate.status]}** — {gate.reason}")
    else:
        lines.append("- ゲート: 未計算")
    lines.append(f"- 確認範囲: {'; '.join(scopes) if scopes else '（記録なし）'}")
    lines.append(f"- 仮定: {'; '.join(assumptions) if assumptions else '（記録なし）'}")
    return "\n".join(lines)


def _render_coverage(findings: Findings) -> str:
    c = findings.coverage
    return "\n".join(
        [
            "| 指標 | 件数 |",
            "| --- | --- |",
            f"| 要求数 | {c.requirements_total} |",
            f"| 承認済み数 | {c.requirements_approved} |",
            f"| 対応範囲数 | {c.requirements_in_scope} |",
            f"| 判定済み数 | {c.requirements_decided} |",
            f"| 未解析数 | {c.requirements_unanalyzed} |",
        ]
    )


def _render_refs(refs: list[SourceRef], *, prefer_symbol: bool) -> str:
    if not refs:
        return "（記録なし）"
    parts: list[str] = []
    for r in refs:
        ident = (r.symbol or r.requirement_id) if prefer_symbol else (r.requirement_id or r.symbol)
        ident = ident or r.path or r.snapshot_id
        if r.line_start is not None:
            ident += f":{r.line_start}"
            if r.line_end is not None and r.line_end != r.line_start:
                ident += f"-{r.line_end}"
        parts.append(ident)
    return ", ".join(parts)


def _render_finding_detail(f: Finding) -> str:
    lines = [f"### {f.requirement_id}: {f.message}", ""]
    lines.append(f"- 判定: {f.verdict}（{VERDICT_LABELS[f.verdict]}）／分類: {f.category}")
    kind = f.counterexample.kind if f.counterexample is not None else "静的"
    lines.append(f"- 根拠種別: {kind}")
    if f.counterexample is not None:
        cex = f.counterexample
        if cex.path_id:
            lines.append(f"- 経路: {cex.path_id}")
        lines.append(f"- 条件: {cex.description}")
        for k, v in sorted(cex.assignments.items()):
            lines.append(f"  - {k} = {v}")
    lines.append(f"- 期待: {f.expected or '（記録なし）'}")
    lines.append(f"- 実装: {f.actual or '（記録なし）'}")
    lines.append(f"- 仕様: {_render_refs(f.spec_refs, prefer_symbol=False)}")
    lines.append(f"- コード: {_render_refs(f.code_refs, prefer_symbol=True)}")
    lines.append(f"- 解析範囲: {f.analysis_scope or '（記録なし）'}")
    lines.append(f"- 仮定: {'; '.join(f.assumptions) if f.assumptions else '（記録なし）'}")
    obs = _TEST_OBSERVATION_LABELS[f.test_observation] if f.test_observation else "未実行"
    lines.append(f"- テスト観測: {obs}")
    if f.verdict in ("UNKNOWN", "REVIEW"):
        lines.append("- 次にできること:")
        if f.next_steps:
            lines.extend(f"  - {step}" for step in f.next_steps)
        else:
            lines.append("  - （記録なし）")
    return "\n".join(lines)


def _render_verified_table(findings: list[Finding]) -> str:
    if not findings:
        return "（なし）"
    lines = ["| 要求ID | 確認範囲 | 概要 |", "| --- | --- | --- |"]
    for f in sorted(findings, key=lambda f: f.requirement_id):
        lines.append(f"| {f.requirement_id} | {f.claim_scope} | {f.message} |")
    return "\n".join(lines)


def render_details(findings: Findings) -> str:
    """VIOLATED → REVIEW → UNKNOWN の詳細 + VERIFIED の一覧表。

    review.md の「詳細」節と check_report.CheckText.text が共有する。
    """
    by_verdict: dict[Verdict, list[Finding]] = {
        "VIOLATED": [],
        "REVIEW": [],
        "UNKNOWN": [],
        "VERIFIED_IN_SCOPE": [],
    }
    for f in findings.findings:
        by_verdict[f.verdict].append(f)

    sections: list[str] = []
    for v in _DETAIL_ORDER:
        items = sorted(by_verdict[v], key=lambda f: f.requirement_id)
        if not items:
            continue
        sections.append(f"## {VERDICT_LABELS[v]}")
        sections.append("")
        for f in items:
            sections.append(_render_finding_detail(f))
            sections.append("")

    verified = by_verdict["VERIFIED_IN_SCOPE"]
    if verified:
        sections.append(f"## {VERDICT_LABELS['VERIFIED_IN_SCOPE']}")
        sections.append("")
        sections.append(_render_verified_table(verified))
        sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def render_review(
    manifest: RunManifest, findings: Findings, gate: GateResult | None, behavior_id: str
) -> str:
    """PoC 13.1（一覧）+ 17.3（対応状況の併記）+ 13.2（詳細）をまとめた review.md 本文。"""
    parts = [
        "## 一覧",
        "",
        _render_overview(manifest, findings, gate, behavior_id),
        "",
        "## 対応状況（PoC 17.3）",
        "",
        _render_coverage(findings),
        "",
        "## 詳細",
        "",
        render_details(findings),
    ]
    return "\n".join(parts)


__all__ = [
    "GATE_STATUS_LABELS",
    "VERDICT_LABELS",
    "render_details",
    "render_review",
]
