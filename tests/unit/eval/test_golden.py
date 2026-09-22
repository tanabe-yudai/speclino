"""Golden 12 ケース（PoC 17.1）が手書き IR とソース入力の両方で期待どおりになる（S09 / S17）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from speclino.eval.runner import main, render_markdown, run_golden

GOLDEN = Path(__file__).resolve().parents[3] / "eval" / "golden" / "cancel_order"


@pytest.mark.parametrize("mode", ["handwritten", "source"])
def test_golden_cases_match(mode: str) -> None:
    report = run_golden(GOLDEN, mode)  # type: ignore[arg-type]
    assert len(report.cases) == 12
    assert report.all_match, [(c.id, c.mismatches, c.counterexample_detail) for c in report.cases]
    assert report.false_verified == 0
    assert report.counterexample_matches == report.counterexample_total == 10
    md = render_markdown(report)
    assert "混同行列" in md and "誤った VERIFIED: **0**" in md


def test_handwritten_and_source_findings_agree() -> None:
    a = run_golden(GOLDEN, "handwritten")
    b = run_golden(GOLDEN, "source")
    assert [(c.id, c.actual) for c in a.cases] == [(c.id, c.actual) for c in b.cases]


def test_main_exit_code(tmp_path: Path) -> None:
    assert main([str(GOLDEN), "--mode", "handwritten", "--out", str(tmp_path / "r.md")]) == 0
    assert (tmp_path / "r.md").exists()
