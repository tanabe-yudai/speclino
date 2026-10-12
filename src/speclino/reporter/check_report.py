"""reporter.check_report（PoC 13 章 / hackathon-plan「判定モデル」の Check summary）。

GitHub Checks に出す title/summary/text と、code_refs に位置情報がある Finding の
annotation を作る。
"""

from __future__ import annotations

from dataclasses import dataclass

from speclino.conformance.gate import GateResult
from speclino.conformance.reasons import VERDICT_PRIORITY
from speclino.domain.finding import CONTENT_HASH_REF, FINGERPRINT_REF, Finding, Findings, Verdict
from speclino.domain.run import GateStatus
from speclino.domain.semantic_diff import SemanticDiff
from speclino.reporter.review_md import (
    _DETAIL_ORDER,
    _TEST_OBSERVATION_LABELS,
    GATE_STATUS_LABELS,
    VERDICT_LABELS,
    _cell,
    _render_refs,
    render_details,
)

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
    """title は「違反 n / 要確認 r / 未検証 u / 適合 k（ゲート: ...）」、summary は 1500 文字以内。

    判定は仕様の 4 値の名前で分けて出す（未検証と要確認を合算しない）。

    意味差分があれば summary に「新規違反 / 解消 / 判定不能化」の件数を添える（PoC 12.2）。
    """
    counts = findings.counts
    gate_label = GATE_STATUS_LABELS[gate.status]
    title = (
        f"違反 {counts.VIOLATED} / 要確認 {counts.REVIEW} / 未検証 {counts.UNKNOWN} / "
        f"適合 {counts.VERIFIED_IN_SCOPE}"
        f"（ゲート: {gate_label}）"
    )

    coverage = findings.coverage
    summary_lines = [
        f"違反 {counts.VIOLATED}件 / 要確認 {counts.REVIEW}件 / 未検証 {counts.UNKNOWN}件 / "
        f"適合 {counts.VERIFIED_IN_SCOPE}件",
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


COMMENT_MARKER = "<!-- speclino:gate -->"
_COMMENT_LIMIT = 60000  # GitHub のコメント上限（65536）に対する余裕
_COMMENT_MAX_ITEMS = 10  # 見出しで出す指摘の上限（残りは詳細へ）
_HEADING_LIMIT = 100  # 見出しに出すメッセージの長さ（全文は根拠に出す）
_GATE_ICONS: dict[GateStatus, str] = {"pass": "✅", "pass_with_exceptions": "✅", "fail": "❌"}
_VERDICT_ICONS: dict[Verdict, str] = {"VIOLATED": "❌", "REVIEW": "⚠️", "UNKNOWN": "❔"}


def render_pr_comment(
    findings: Findings,
    gate: GateResult,
    details_url: str | None = None,
    semantic_diff: SemanticDiff | None = None,
) -> str:
    """PR の会話タブに出す判定の要約。ゲートを止めた指摘だけを短く出す。

    先頭に `COMMENT_MARKER` を置き、push のたびに新しいコメントを作らず同じものを
    更新できるようにする（`CodeHost.upsert_comment`）。適合の一覧と全項目の詳細は
    Checks タブ（`render_check`）と web に任せ、ここでは何を直せばよいかだけを読めるようにする。
    """
    counts = findings.counts
    link = f"[詳細を開く]({details_url})" if details_url else "詳細は Checks タブを参照"
    status = f"{_GATE_ICONS[gate.status]} SpecLino / gate: {GATE_STATUS_LABELS[gate.status]}"
    tally = [
        f"{VERDICT_LABELS[v]} {getattr(counts, v)}" for v in _DETAIL_ORDER if getattr(counts, v)
    ]
    if not tally and gate.status != "fail":
        return "\n".join(
            [COMMENT_MARKER, f"### {status} — 適合 {counts.VERIFIED_IN_SCOPE} 件", "", link, ""]
        )

    parts = [
        COMMENT_MARKER,
        f"### {status} — {' ・ '.join(tally) if tally else gate.reason}",
        "",
        f"適合 {counts.VERIFIED_IN_SCOPE} 件は省略しています。{link}",
        "",
    ]
    blocking = _by_verdict(gate.blocking)
    for f in blocking[:_COMMENT_MAX_ITEMS]:
        parts += [_render_comment_finding(f), ""]
    if len(blocking) > _COMMENT_MAX_ITEMS:
        parts += [f"ほか {len(blocking) - _COMMENT_MAX_ITEMS} 件は{link}で確認してください。", ""]

    blocking_ids = {f.id for f in gate.blocking}
    notes = {f.id: "免除中" for f in gate.waived} | {f.id: "確定済み" for f in gate.cleared}
    notes |= {f.id: "助言" for f in gate.advisory}
    rest = _by_verdict(
        [
            f
            for f in findings.findings
            if f.verdict != "VERIFIED_IN_SCOPE" and f.id not in blocking_ids
        ]
    )
    if rest:
        parts += [f"<details><summary>ゲートを止めていない指摘（{len(rest)} 件）</summary>", ""]
        for f in rest:
            note = f"（{notes[f.id]}）" if f.id in notes else ""
            parts.append(
                f"- {_VERDICT_ICONS[f.verdict]} {f.requirement_id}{note}: {_cell(f.message)}"
            )
        parts += ["", "</details>", ""]

    parts += _render_comment_tally(findings, semantic_diff)
    body = "\n".join(parts)
    if len(body) > _COMMENT_LIMIT:
        body = body[:_COMMENT_LIMIT] + f"\n\n…（省略。{link}）\n"
    return body


def _by_verdict(items: list[Finding]) -> list[Finding]:
    order = {v: i for i, v in enumerate(_DETAIL_ORDER)}
    return sorted(items, key=lambda f: (order.get(f.verdict, len(order)), f.requirement_id))


def _render_comment_finding(f: Finding) -> str:
    """1 件を「見出し・期待と実装・場所・次にできること」に絞り、根拠は畳む。"""
    # メッセージは「要点: 詳細」の形が多い。見出しは要点だけにし、全文は根拠に出す
    message = _cell(f.message)
    short = message.split(": ", 1)[0] or message
    if len(short) > _HEADING_LIMIT:
        short = short[:_HEADING_LIMIT] + "…"
    lines = [f"#### {_VERDICT_ICONS.get(f.verdict, '')} {f.requirement_id} {short}"]
    if f.expected and f.actual:
        lines.append(f"- 期待: {f.expected} → 実装: {f.actual}")
    elif f.expected:
        lines.append(f"- 期待: {f.expected}")
    elif f.actual:
        lines.append(f"- 実装: {f.actual}")
    if f.code_refs:
        lines.append(f"- 場所: `{_render_refs(f.code_refs, prefer_symbol=True)}`")
    if f.verdict in ("UNKNOWN", "REVIEW") and f.next_steps:
        lines.append(f"- 次にできること: {' / '.join(f.next_steps)}")

    evidence = [f"- 判定: {f.verdict}（{VERDICT_LABELS[f.verdict]}）／分類: {f.category}"]
    if short != message:
        evidence.append(f"- 全文: {message}")
    if f.counterexample is not None:
        cex = f.counterexample
        evidence.append(f"- 根拠種別: {cex.kind}")
        if cex.path_id:
            evidence.append(f"- 経路: {cex.path_id}")
        evidence.append(f"- 条件: {cex.description}")
        evidence.extend(f"  - {k} = {v}" for k, v in sorted(cex.assignments.items()))
    if f.spec_refs:
        evidence.append(f"- 仕様: {_render_refs(f.spec_refs, prefer_symbol=False)}")
    if f.analysis_scope:
        evidence.append(f"- 解析範囲: {f.analysis_scope}")
    if f.assumptions:
        evidence.append(f"- 仮定: {'; '.join(f.assumptions)}")
    if f.test_observation:
        evidence.append(f"- テスト観測: {_TEST_OBSERVATION_LABELS[f.test_observation]}")
    if (single := _single_attest_command(f)) is not None:
        evidence.append(f"- この 1 件だけ確定: `{single}`（問題として記録するなら末尾に ` ack`）")
    lines += ["", "<details><summary>根拠</summary>", "", *evidence, "", "</details>"]
    return "\n".join(lines)


def _single_attest_command(f: Finding) -> str | None:
    """指紋と内容の hash を持つ REVIEW を 1 件だけ確定する PR コメント。無ければ None。"""
    if f.verdict != "REVIEW":
        return None
    fp = next(
        (r.removeprefix(FINGERPRINT_REF) for r in f.evidence_refs if r.startswith(FINGERPRINT_REF)),
        None,
    )
    ch = next(
        (
            r.removeprefix(CONTENT_HASH_REF)
            for r in f.evidence_refs
            if r.startswith(CONTENT_HASH_REF)
        ),
        None,
    )
    return f"/speclino attest {fp} {ch}" if fp and ch else None


def _render_comment_tally(findings: Findings, semantic_diff: SemanticDiff | None) -> list[str]:
    c = findings.coverage
    lines = [
        "<details><summary>集計</summary>",
        "",
        f"要求数 {c.requirements_total} / 承認済み {c.requirements_approved} / "
        f"対応範囲 {c.requirements_in_scope} / 判定済み {c.requirements_decided} / "
        f"未解析 {c.requirements_unanalyzed}",
    ]
    if semantic_diff is not None and semantic_diff.base_available:
        d = semantic_diff.counts
        lines.append(
            f"base との差分: 新規違反 {d.new_violation} / 既存違反 {d.existing_violation} / "
            f"解消 {d.resolved_violation} / 判定不能化 {d.became_undecidable}"
        )
    return [*lines, "", "</details>", ""]


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


__all__ = [
    "COMMENT_MARKER",
    "CheckText",
    "check_annotations",
    "render_check",
    "render_pr_comment",
]
