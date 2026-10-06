"""PR の会話タブに出すゲートのコメント（render_pr_comment）。ダメだった部分だけを短く出す。"""

from __future__ import annotations

from speclino.conformance.engine import evaluate
from speclino.conformance.gate import GateResult, aggregate_gate
from speclino.domain.finding import Findings
from speclino.domain.spec_ir import RuleLevel
from speclino.reporter.check_report import COMMENT_MARKER, render_pr_comment
from tests.fixtures import cancel_order as co

URL = "https://example.test/runs/1"


def _gate(findings: Findings, levels: dict[str, RuleLevel] | None = None) -> GateResult:
    return aggregate_gate(findings, levels or {r.id: r.level for r in co.SPEC.rules})


def test_failed_comment_lists_only_what_blocks_the_gate() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    body = render_pr_comment(findings, _gate(findings), URL)
    assert body.startswith(COMMENT_MARKER)
    assert "SpecLino / gate: 失敗" in body
    assert "R-CANCEL-04" in body and "inventory_restore_command" in body
    # 適合は一覧にしない（件数だけ）。詳細は Checks タブと web
    assert "| 要求ID |" not in body
    assert f"適合 {findings.counts.VERIFIED_IN_SCOPE} 件は省略" in body
    assert URL in body
    # 中身の無い項目は出さず、根拠は畳む
    assert "（記録なし）" not in body
    assert "<details><summary>根拠</summary>" in body


def test_passed_comment_is_one_line_and_a_link() -> None:
    findings = evaluate(co.SPEC, co.CORRECT, co.DOMAIN)
    body = render_pr_comment(findings, _gate(findings), URL)
    assert "SpecLino / gate: 通過" in body
    assert f"適合 {findings.counts.VERIFIED_IN_SCOPE} 件" in body
    assert "####" not in body and "<details>" not in body
    assert len(body) < 300


def test_non_blocking_findings_are_folded_away() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    advisory = {r.id: "advisory" for r in co.SPEC.rules}
    body = render_pr_comment(findings, _gate(findings, advisory), URL)  # type: ignore[arg-type]
    assert "#### " not in body.split("<details><summary>ゲートを止めていない指摘")[0]
    assert "ゲートを止めていない指摘" in body and "R-CANCEL-04" in body


def test_many_blocking_findings_are_capped() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    base = next(f for f in findings.findings if f.verdict == "VIOLATED")
    many = [
        base.model_copy(update={"id": f"{base.id}-{i}", "requirement_id": f"R-X-{i:02d}"})
        for i in range(13)
    ]
    findings = findings.model_copy(update={"findings": many})
    levels: dict[str, RuleLevel] = {f.requirement_id: "mandatory" for f in many}
    body = render_pr_comment(findings, _gate(findings, levels), URL)
    assert body.count("\n#### ") == 10
    assert "ほか 3 件" in body


def test_long_message_is_shortened_in_the_heading() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    base = next(f for f in findings.findings if f.verdict == "VIOLATED")
    long = base.model_copy(update={"message": "宣言済みテストが失敗した: " + "t" * 300})
    findings = findings.model_copy(update={"findings": [long]})
    body = render_pr_comment(findings, _gate(findings), URL)
    heading = next(line for line in body.splitlines() if line.startswith("#### "))
    assert heading.endswith("宣言済みテストが失敗した")  # 「要点: 詳細」の要点だけ
    assert "t" * 300 in body  # 全文は根拠の中に残す


def test_heading_without_a_summary_part_is_cut_at_the_limit() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    base = next(f for f in findings.findings if f.verdict == "VIOLATED")
    long = base.model_copy(update={"message": "x" * 300})
    findings = findings.model_copy(update={"findings": [long]})
    body = render_pr_comment(findings, _gate(findings), URL)
    heading = next(line for line in body.splitlines() if line.startswith("#### "))
    assert len(heading) < 140 and heading.endswith("…")
