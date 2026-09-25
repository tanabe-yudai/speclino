"""Golden 27 ケース（PoC 17.1）が手書き IR とソース入力の両方で期待どおりになる（S09 / S17 /
S34）。holdout 3 ケース（調整に使わない検証用、`make eval` には含めない）と、凍結入力の
再現性（PoC 12.3）も併せて確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from speclino.eval.runner import EvalReport, main, render_markdown, run_golden

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "eval" / "golden" / "cancel_order"
HOLDOUT = ROOT / "eval" / "holdout" / "cancel_order"


@pytest.mark.parametrize("mode", ["handwritten", "source"])
def test_golden_cases_match(mode: str) -> None:
    report = run_golden(GOLDEN, mode)  # type: ignore[arg-type]
    assert len(report.cases) == 29
    assert report.all_match, [(c.id, c.mismatches, c.counterexample_detail) for c in report.cases]
    assert report.false_verified == 0
    assert report.counterexample_matches == report.counterexample_total == 16
    # 有限ドメイン検算（S35）: Z3 が VERIFIED / VIOLATED と断定した要求は総当たりでも同じ結論
    assert report.finite_check_disagreements == 0 and report.finite_check_agreements > 150
    md = render_markdown(report)
    assert "混同行列" in md and "誤った VERIFIED: **0**" in md


def test_handwritten_and_source_findings_agree() -> None:
    a = run_golden(GOLDEN, "handwritten")
    b = run_golden(GOLDEN, "source")
    assert [(c.id, c.actual) for c in a.cases] == [(c.id, c.actual) for c in b.cases]


def test_main_exit_code(tmp_path: Path) -> None:
    assert main([str(GOLDEN), "--mode", "handwritten", "--out", str(tmp_path / "r.md")]) == 0
    assert (tmp_path / "r.md").exists()


@pytest.mark.parametrize("mode", ["handwritten", "source"])
def test_holdout_cases_match(mode: str) -> None:
    """S34: holdout は golden の調整に使わない検証用ケース。`make eval` には含めない
    （Makefile の `eval` ターゲットは `eval/golden/cancel_order` だけを渡す）が、同じ
    runner・同じ形式で source / handwritten 両モードから評価できることを確認する。
    """
    report = run_golden(HOLDOUT, mode)  # type: ignore[arg-type]
    assert len(report.cases) == 3
    assert report.all_match, [(c.id, c.mismatches, c.counterexample_detail) for c in report.cases]
    assert report.false_verified == 0


@pytest.mark.parametrize("mode", ["handwritten", "source"])
def test_frozen_rerun_reproducible(mode: str) -> None:
    """S34-25 frozen_rerun: 同じ凍結入力（golden 全体）を 2 回評価しても、時刻や run_id を
    除いた findings（verdict / category /反例）が一致する（PoC 12.3 / 17.1）。
    """

    def _key(report: EvalReport) -> list[tuple[object, ...]]:
        return [
            (c.id, c.expected, c.actual, c.mismatches, c.counterexample_ok, c.counterexample_detail)
            for c in report.cases
        ]

    a = run_golden(GOLDEN, mode)  # type: ignore[arg-type]
    b = run_golden(GOLDEN, mode)  # type: ignore[arg-type]
    assert _key(a) == _key(b)
    assert a.confusion == b.confusion
    assert a.false_verified == b.false_verified
    assert a.counterexample_matches == b.counterexample_matches
    assert a.counterexample_total == b.counterexample_total


def test_metrics_in_report() -> None:
    """S34: render_markdown が区分ごとの検出率・UNKNOWN率・REVIEW率・反例再現率を含む
    （PoC 17.3「一致率だけを出さない」）。
    """
    report = run_golden(GOLDEN, "source")
    assert report.violated_detection_rate == pytest.approx(1.0)
    assert report.unknown_rate is not None and 0 < report.unknown_rate < 1
    assert report.review_rate is not None and 0 < report.review_rate < 1
    assert report.counterexample_reproduction_rate == pytest.approx(1.0)
    md = render_markdown(report)
    assert "既知違反の検出率" in md
    assert "UNKNOWN 率" in md
    assert "要確認率" in md
    assert "反例の再現率" in md
