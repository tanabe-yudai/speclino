"""reporter.check_report（PoC 13 章 / hackathon-plan「判定モデル」の Check summary）。

GitHub Checks に出す title/summary/text と、code_refs に位置情報がある Finding の
annotation を作る。
"""

from __future__ import annotations

from dataclasses import dataclass

from speclino.conformance.gate import GateResult
from speclino.conformance.reasons import VERDICT_PRIORITY
from speclino.domain.finding import Finding, Findings, Verdict
from speclino.domain.semantic_diff import SemanticDiff
from speclino.reporter.review_md import GATE_STATUS_LABELS, render_details

_SUMMARY_LIMIT = 1500  # GitHub Checks summary の目安
_TRUNCATE_MARKER = "\n…（省略。詳細は text を参照）"

# VERIFIED_IN_SCOPE は annotation を出さない。
_ANNOTATION_LEVEL: dict[Verdict, str] = {
    "VIOLATED": "failure",
    "UNKNOWN": "warning",
    "REVIEW": "warning",
}


@dataclass(frozen=True)
class CheckText:
    title: str
    summary: str
    text: str


@dataclass(frozen=True)
class _Location:
    path: str
    start_line: int
    end_line: int


def _first_location(finding: Finding) -> _Location | None:
    for ref in finding.code_refs:
        if ref.path is not None and ref.line_start is not None:
            return _Location(ref.path, ref.line_start, ref.line_end or ref.line_start)
    return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATE_MARKER)] + _TRUNCATE_MARKER


def render_check(
    findings: Findings, gate: GateResult, semantic_diff: SemanticDiff | None = None
) -> CheckText:
    """title は「違反 n / 未検証 m / 適合 k（ゲート: ...）」、summary は 1500 文字以内。

    意味差分があれば summary に「新規違反 / 解消 / 判定不能化」の件数を添える（PoC 12.2）。
    """
    counts = findings.counts
    unresolved = counts.UNKNOWN + counts.REVIEW
    gate_label = GATE_STATUS_LABELS[gate.status]
    title = (
        f"違反 {counts.VIOLATED} / 未検証 {unresolved} / 適合 {counts.VERIFIED_IN_SCOPE}"
        f"（ゲート: {gate_label}）"
    )

    coverage = findings.coverage
    summary_lines = [
        f"違反 {counts.VIOLATED}件 / 未検証 {unresolved}件"
        f"（不明 {counts.UNKNOWN}・要確認 {counts.REVIEW}） / 適合 {counts.VERIFIED_IN_SCOPE}件",
        f"ゲート: {gate_label} — {gate.reason}",
        "",
        f"要求数 {coverage.requirements_total} / 承認済み {coverage.requirements_approved} / "
        f"対応範囲 {coverage.requirements_in_scope} / 判定済み {coverage.requirements_decided} / "
        f"未解析 {coverage.requirements_unanalyzed}",
    ]
    if semantic_diff is not None and semantic_diff.base_available:
        c = semantic_diff.counts
        summary_lines.append(
            f"base との差分: 新規違反 {c.new_violation} / 既存違反 {c.existing_violation} / "
            f"解消 {c.resolved_violation} / 判定不能化 {c.became_undecidable}"
        )
    if gate.blocking:
        blocking_ids = ", ".join(f.requirement_id for f in gate.blocking[:10])
        summary_lines.append("")
        summary_lines.append(f"必須ルール非成立: {blocking_ids}")

    summary = _truncate("\n".join(summary_lines), _SUMMARY_LIMIT)
    return CheckText(title=title, summary=summary, text=render_details(findings))


def check_annotations(findings: Findings, max_items: int = 50) -> list[dict[str, object]]:
    """GitHub Checks の annotation 形式。VERIFIED と path/line の無い Finding は出さない。"""
    candidates = [f for f in findings.findings if f.verdict in _ANNOTATION_LEVEL]
    candidates.sort(key=lambda f: (VERDICT_PRIORITY[f.verdict], f.requirement_id))

    result: list[dict[str, object]] = []
    for f in candidates:
        if len(result) >= max_items:
            break
        loc = _first_location(f)
        if loc is None:
            continue
        result.append(
            {
                "path": loc.path,
                "start_line": loc.start_line,
                "end_line": loc.end_line,
                "annotation_level": _ANNOTATION_LEVEL[f.verdict],
                "message": f.message,
                "title": f"{f.requirement_id} ({f.verdict})",
            }
        )
    return result


__all__ = ["CheckText", "check_annotations", "render_check"]
