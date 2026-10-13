"""reporter の単体試験。cancel_order フィクスチャと本物の engine/gate を使う。

json_out / review_md / check_report をそれぞれ、engine.evaluate と gate.aggregate_gate の
実際の出力に対して検証する。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from speclino.conformance.engine import evaluate
from speclino.conformance.gate import GateResult, aggregate_gate
from speclino.conformance.semantic_diff import DiffSide, semantic_diff
from speclino.domain.code_ir import AnalysisLimits, Outcome, UnknownCause
from speclino.domain.code_ir import Path as CodePath
from speclino.domain.finding import Findings
from speclino.domain.run import RunInputs, RunManifest, RunVersions
from speclino.domain.spec_ir import RuleLevel
from speclino.reporter.check_report import check_annotations, render_check
from speclino.reporter.json_out import write_artifacts
from speclino.reporter.review_md import render_review
from tests.fixtures import cancel_order as co


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="run-test-0001",
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
        inputs=RunInputs(head_commit="head-sha", base_commit="base-sha"),
        versions=RunVersions(speclino="0.1.0", analyzer="handwritten", engine="0.1.0", solver="z3"),
        limits=AnalysisLimits(depth=2, max_paths=64, solver_timeout_ms=500),
        fail_on=["VIOLATED", "UNKNOWN", "REVIEW"],
        mode="blocking",
    )


def _levels() -> dict[str, RuleLevel]:
    return {r.id: r.level for r in co.SPEC.rules}


def _gate_for(findings: Findings) -> GateResult:
    return aggregate_gate(findings, _levels())


def _incomplete_findings() -> Findings:
    """R-CANCEL-04/03 が UNKNOWN になる経路（next_steps を持つ）。"""
    incomplete = co.code_ir(
        [
            co.P_FORBIDDEN,
            co.P_CONFLICT,
            CodePath(
                path_id="p3_success",
                guard=co.SUCCESS,
                state_updates=co.SUCCESS_UPDATES,
                events=[co.write_status(1), co.call_audit(2)],
                outcome=Outcome(kind="http_response", status=204),
                precision="incomplete",
                unknown_causes=[
                    UnknownCause(code="unresolved_call", detail="helper() を解決できない")
                ],
                source_refs=[co.CODE_SRC],
            ),
        ]
    )
    return evaluate(co.SPEC, incomplete, co.DOMAIN)


# ---------------------------------------------------------------- json_out


def test_write_artifacts_writes_expected_files_deterministically(tmp_path: Path) -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    gate = _gate_for(findings)
    manifest = _manifest()

    out1 = write_artifacts(
        tmp_path / "run1", manifest, co.SPEC, co.RESTORE_ADMIN_ONLY, findings, gate
    )
    expected_names = {
        "run-manifest.json",
        "spec-ir.json",
        "code-ir.json",
        "findings.json",
        "gate.json",
        "review.md",
    }
    assert set(out1) == expected_names
    for path in out1.values():
        assert path.exists()

    out2 = write_artifacts(
        tmp_path / "run2", manifest, co.SPEC, co.RESTORE_ADMIN_ONLY, findings, gate
    )
    for name in expected_names:
        assert out1[name].read_bytes() == out2[name].read_bytes(), name

    gate_data = json.loads(out1["gate.json"].read_text(encoding="utf-8"))
    assert gate_data["status"] == gate.status
    assert gate_data["blocking_ids"] == sorted(f.id for f in gate.blocking)
    assert gate_data["advisory_ids"] == sorted(f.id for f in gate.advisory)
    assert gate_data["waived_ids"] == sorted(f.id for f in gate.waived)

    manifest_data = json.loads(out1["run-manifest.json"].read_text(encoding="utf-8"))
    assert manifest_data["run_id"] == "run-test-0001"


def test_gate_json_records_waivers_with_expiry_and_fix(tmp_path: Path) -> None:
    """例外で免除した違反は、どの例外で・いつまで・どう直すかを gate.json に残す。

    画面の「免除中」に期限と直し方を出すため。
    """
    from datetime import date

    from speclino.conformance.exceptions import Waiver

    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    violated = next(f for f in findings.findings if f.requirement_id == "R-CANCEL-04")
    gate = aggregate_gate(findings, _levels(), waived_ids=frozenset({violated.id}))
    waiver = Waiver(
        violated.id, "R-CANCEL-04", "EXC-1", "owner", date(2026, 12, 1), "理由", "直し方"
    )
    # ゲートで免除されていない finding の例外は書かない
    stray = Waiver("F-unused", "R-X", "EXC-2", "owner", date(2026, 12, 1), "理由", "直し方")
    out = write_artifacts(
        tmp_path, _manifest(), co.SPEC, None, findings, gate, waivers=[waiver, stray]
    )
    data = json.loads(out["gate.json"].read_text(encoding="utf-8"))
    assert data["waivers"] == [
        {
            "finding_id": violated.id,
            "requirement_id": "R-CANCEL-04",
            "exception_id": "EXC-1",
            "approver": "owner",
            "expires_on": "2026-12-01",
            "reason": "理由",
            "alternative": "直し方",
        }
    ]
    plain = write_artifacts(tmp_path / "plain", _manifest(), co.SPEC, None, findings, gate)
    assert json.loads(plain["gate.json"].read_text(encoding="utf-8"))["waivers"] == []


def test_write_artifacts_skips_optional_none_inputs(tmp_path: Path) -> None:
    findings = evaluate(co.SPEC, co.CORRECT, co.DOMAIN)
    gate = _gate_for(findings)
    out = write_artifacts(tmp_path, _manifest(), co.SPEC, None, findings, gate)
    assert "code-ir.json" not in out
    assert "semantic-diff.json" not in out
    assert "findings.json" in out

    out_no_gate = write_artifacts(tmp_path / "no-gate", _manifest(), co.SPEC, None, findings, None)
    assert "gate.json" not in out_no_gate
    assert "review.md" in out_no_gate
    assert "未計算" in out_no_gate["review.md"].read_text(encoding="utf-8")


def test_write_artifacts_includes_semantic_diff_when_given(tmp_path: Path) -> None:
    base_findings = evaluate(co.SPEC, co.CORRECT, co.DOMAIN)
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    gate = _gate_for(findings)
    diff = semantic_diff(
        DiffSide("base", [co.SPEC], [co.CORRECT], base_findings),
        DiffSide("head", [co.SPEC], [co.RESTORE_ADMIN_ONLY], findings),
    )
    out = write_artifacts(
        tmp_path, _manifest(), co.SPEC, co.RESTORE_ADMIN_ONLY, findings, gate, diff
    )
    assert "semantic-diff.json" in out
    data = json.loads(out["semantic-diff.json"].read_text(encoding="utf-8"))
    assert data["counts"]["new_violation"] == 1
    review = out["review.md"].read_text(encoding="utf-8")
    assert "## 意味差分（base → head）" in review
    assert "| R-CANCEL-04 | VERIFIED_IN_SCOPE | VIOLATED | 新規違反 |" in review
    assert "- p3_success" in review and "+ p4_owner" in review
    check = render_check(findings, gate, diff)
    assert "新規違反 1 / 既存違反 0 / 解消 0 / 判定不能化 0" in check.summary


def test_review_md_explains_missing_base() -> None:
    findings = evaluate(co.SPEC, co.CORRECT, co.DOMAIN)
    diff = semantic_diff(
        None, DiffSide("head", [co.SPEC], [co.CORRECT], findings), base_error="仕様が無い"
    )
    text = render_review(_manifest(), findings, _gate_for(findings), "cancel_order", diff)
    assert "base 側の判定が無いため差分を出せない（仕様が無い）" in text
    assert "base との差分" not in render_check(findings, _gate_for(findings), diff).summary


# ---------------------------------------------------------------- review_md


def test_review_md_contains_violation_detail() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    gate = _gate_for(findings)
    text = render_review(_manifest(), findings, gate, co.SPEC.behavior_id)
    assert "R-CANCEL-04" in text
    assert "VIOLATED" in text
    assert "inventory_restore_command" in text
    assert "p4_owner" in text


def test_review_md_next_steps_for_unknown_findings() -> None:
    findings = _incomplete_findings()
    assert findings.counts.UNKNOWN > 0
    gate = _gate_for(findings)
    text = render_review(_manifest(), findings, gate, co.SPEC.behavior_id)
    assert "次にできること" in text


def test_review_md_orders_and_summarizes_verified() -> None:
    findings = evaluate(co.SPEC, co.CORRECT, co.DOMAIN)
    gate = _gate_for(findings)
    text = render_review(_manifest(), findings, gate, co.SPEC.behavior_id)
    # 全件適合: VERIFIED の詳細セクションは表だけで、他の verdict の見出しは出ない
    assert "## 違反" not in text
    assert "## 要確認" not in text
    assert "## 未検証" not in text
    assert "## 適合" in text
    # 「適合」は検査器が確認した範囲での適合（表示名を短くしても意味は注記で残す）
    assert "検査器が確認した範囲" in text
    assert "| 要求ID | 確認範囲 | 概要 |" in text


# ---------------------------------------------------------------- check_report


def test_render_check_title_and_summary_length() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    gate = _gate_for(findings)
    check = render_check(findings, gate)
    assert str(findings.counts.VIOLATED) in check.title
    assert str(findings.counts.VERIFIED_IN_SCOPE) in check.title
    assert "ゲート" in check.title
    # 判定は仕様の 4 値の名前で分けて出す（未検証と要確認を合算しない。「不明」とは書かない）
    c = findings.counts
    assert (
        f"違反 {c.VIOLATED} / 要確認 {c.REVIEW} / 未検証 {c.UNKNOWN} / 適合 {c.VERIFIED_IN_SCOPE}"
        in check.title
    )
    assert check.summary.startswith(
        f"違反 {c.VIOLATED}件 / 要確認 {c.REVIEW}件 / 未検証 {c.UNKNOWN}件 / "
        f"適合 {c.VERIFIED_IN_SCOPE}件"
    )
    assert "不明" not in check.title + check.summary
    assert len(check.summary) <= 1500
    assert "R-CANCEL-04" in check.text


def test_render_check_summary_stays_under_limit_with_many_blocking_findings() -> None:
    findings = _incomplete_findings()
    gate = _gate_for(findings)
    check = render_check(findings, gate)
    assert len(check.summary) <= 1500


def test_annotations_excludes_verified_and_findings_without_location() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    violated = next(f for f in findings.findings if f.verdict == "VIOLATED")
    assert violated.requirement_id == "R-CANCEL-04"

    located_ref = violated.code_refs[0].model_copy(
        update={
            "path": "examples/orders/app/service.py",
            "line_start": 42,
            "line_end": 44,
        }
    )
    patched = violated.model_copy(update={"code_refs": [located_ref]})
    patched_findings = findings.model_copy(
        update={"findings": [patched if f.id == violated.id else f for f in findings.findings]}
    )

    anns = check_annotations(patched_findings)
    # 元の Findings の code_refs には symbol しか無いので、位置が付いた 1 件だけが残る
    assert len(anns) == 1
    ann = anns[0]
    assert ann["path"] == "examples/orders/app/service.py"
    assert ann["start_line"] == 42
    assert ann["end_line"] == 44
    assert ann["annotation_level"] == "failure"
    assert ann["title"] == "R-CANCEL-04 (VIOLATED)"

    verified_ids = {f.requirement_id for f in findings.findings if f.verdict == "VERIFIED_IN_SCOPE"}
    ann_ids = {a["title"] for a in anns}
    assert not any(vid in aid for vid in verified_ids for aid in ann_ids)


def test_annotations_respects_max_items() -> None:
    findings = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    violated = next(f for f in findings.findings if f.verdict == "VIOLATED")
    located_ref = violated.code_refs[0].model_copy(
        update={"path": "examples/orders/app/service.py", "line_start": 42}
    )
    patched = violated.model_copy(update={"code_refs": [located_ref]})
    patched_findings = findings.model_copy(
        update={"findings": [patched if f.id == violated.id else f for f in findings.findings]}
    )
    assert check_annotations(patched_findings, max_items=0) == []


def test_review_md_names_unknown_as_unverified() -> None:
    """review.md の判定の名前は画面と同じ 4 値（UNKNOWN は「未検証」）。"""
    from speclino.reporter.review_md import VERDICT_LABELS

    assert VERDICT_LABELS == {
        "VERIFIED_IN_SCOPE": "適合",
        "VIOLATED": "違反",
        "UNKNOWN": "未検証",
        "REVIEW": "要確認",
    }


def test_gate_label_says_exempted_not_exceptions() -> None:
    """例外付き通過は画面と同じく「通過（免除中あり）」と書く（言葉を「免除中」に揃える）。"""
    from speclino.reporter.review_md import GATE_STATUS_LABELS

    assert GATE_STATUS_LABELS["pass_with_exceptions"] == "通過（免除中あり）"
