"""reporter.review_md（PoC 13 章「レポートの仕様」/ 17.3「一致率だけを出さない」）。

Findings と GateResult を人間向け Markdown（`review.md`）に圧縮する。GitHub Checks の
summary/text にも使うため見出しは `##` 以下、表は GFM で書く。
"""

from __future__ import annotations

from speclino.conformance.gate import GateResult
from speclino.domain.common import SourceRef
from speclino.domain.finding import Finding, Findings, TestObservation, Verdict
from speclino.domain.run import GateStatus, RunManifest
from speclino.domain.semantic_diff import (
    BehaviorDelta,
    FindingChange,
    PathSummary,
    RuleDelta,
    SemanticDiff,
)

VERDICT_LABELS: dict[Verdict, str] = {
    "VERIFIED_IN_SCOPE": "適合（確認範囲内）",
    "VIOLATED": "違反",
    "UNKNOWN": "未検証",
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
    if manifest.analysis:
        reused = sum(1 for a in manifest.analysis if a.reused_from_base)
        lines.append(f"- 再利用: {reused} / {len(manifest.analysis)} behavior（S41c 増分解析）")
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
    lines = [f"### {f.requirement_id}: {_cell(f.message)}", ""]
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
        lines.append(f"| {f.requirement_id} | {f.claim_scope} | {_cell(f.message)} |")
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


_FINDING_CHANGE_LABELS: dict[FindingChange, str] = {
    "new_violation": "新規違反",
    "existing_violation": "既存違反",
    "resolved_violation": "解消された違反",
    "became_undecidable": "判定不能化",
    "became_decidable": "判定可能化",
    "added": "追加された判定",
    "removed": "削除された判定",
    "changed": "その他の変化",
    "unchanged": "変化なし",
}
_CHANGE_LABELS = {"added": "追加", "changed": "変更", "removed": "削除", "unchanged": "変化なし"}
_PATH_LIMIT = 8  # 経路の一覧は長くなるので上限を設ける（全量は semantic-diff.json）


def _render_rule_changes(rules: list[RuleDelta]) -> list[str]:
    changed = [r for r in rules if r.status != "unchanged"]
    if not changed:
        return ["（なし）"]
    return [f"- {r.rule_id}: {_CHANGE_LABELS[r.status]}" for r in changed]


def _render_path_lines(sign: str, paths: list[PathSummary]) -> list[str]:
    """経路の一覧（sign は追加 `+` / 削除 `-`）。長くなるので `_PATH_LIMIT` で打ち切る。"""
    lines: list[str] = []
    for p in paths[:_PATH_LIMIT]:
        events = "; ".join(p.events) if p.events else "（イベントなし）"
        lines.append(f"  - {sign} {p.path_id}: {_cell(p.guard)} → {p.outcome} / {_cell(events)}")
    if len(paths) > _PATH_LIMIT:
        lines.append(f"  - {sign} …他 {len(paths) - _PATH_LIMIT} 経路")
    return lines


def _render_behavior_changes(behaviors: list[BehaviorDelta]) -> list[str]:
    changed = [b for b in behaviors if b.status != "unchanged"]
    if not changed:
        return ["（なし。経路の内容は base と同じ）"]
    lines: list[str] = []
    for b in changed:
        n_add, n_del = len(b.added_paths), len(b.removed_paths)
        lines.append(
            f"- `{b.behavior_id}`: {_CHANGE_LABELS[b.status]}"
            f"（追加 {n_add} / 削除 {n_del} / 同一 {b.unchanged_paths}）"
        )
        lines += _render_path_lines("+", b.added_paths)
        lines += _render_path_lines("-", b.removed_paths)
    return lines


def render_semantic_diff(diff: SemanticDiff) -> str:
    """PoC 12.2 の結果分類（新規／既存／解消／判定不能化、要求と実装の差分）。"""
    if not diff.base_available:
        reason = f"（{diff.base_error}）" if diff.base_error else ""
        return f"base 側の判定が無いため差分を出せない{reason}。head の判定を「追加」として扱う。"
    lines = [
        f"- 比較: `{diff.base_commit}` → `{diff.head_commit}`",
        "",
        "| 区分 | 件数 |",
        "| --- | --- |",
    ]
    lines += [
        f"| {label} | {getattr(diff.counts, status)} |"
        for status, label in _FINDING_CHANGE_LABELS.items()
    ]
    notable = [d for d in diff.findings if d.status not in ("unchanged", "added")]
    if notable:
        lines += ["", "| 要求ID | base | head | 区分 |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {d.requirement_id} | {d.base_verdict or '-'} | {d.head_verdict or '-'}"
            f" | {_FINDING_CHANGE_LABELS[d.status]} |"
            for d in notable
        ]
    lines += ["", "### 要求の変更", "", *_render_rule_changes(diff.rules)]
    lines += ["", "### 実装の振る舞いの変更", "", *_render_behavior_changes(diff.behaviors)]
    return "\n".join(lines)


def render_review(
    manifest: RunManifest,
    findings: Findings,
    gate: GateResult | None,
    behavior_id: str,
    semantic_diff: SemanticDiff | None = None,
) -> str:
    """PoC 13.1（一覧）+ 17.3（対応状況の併記）+ 12.2（意味差分）+ 13.2（詳細）の review.md。"""
    parts = [
        "## 一覧",
        "",
        _render_overview(manifest, findings, gate, behavior_id),
        "",
        "## 対応状況（PoC 17.3）",
        "",
        _render_coverage(findings),
        "",
    ]
    if semantic_diff is not None:
        parts += ["## 意味差分（base → head）", "", render_semantic_diff(semantic_diff), ""]
    parts += ["## 詳細", "", render_details(findings)]
    return "\n".join(parts)


__all__ = [
    "GATE_STATUS_LABELS",
    "VERDICT_LABELS",
    "render_details",
    "render_review",
    "render_semantic_diff",
]


def _cell(text: str) -> str:
    """GFM の表セル用に改行と縦棒を無害化する（1 行の概要にする）。"""
    return " ".join(part.strip() for part in text.splitlines() if part.strip()).replace("|", "\\|")
