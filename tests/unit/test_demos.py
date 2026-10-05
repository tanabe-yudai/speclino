"""デモ PR の題材（S27）が期待どおりの判定になることを固定する。

本番の PR を作る前に、同じ変異をローカルで検査する。実装を変えてデモが壊れれば CI で気付く。
デモ ③（自己検証）は `test_self_verification.py`、④（ガバナンス）は
`tests/integration/services/test_api_flow.py` が担当する。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from speclino.analysis.limits import DEFAULT_LIMITS, MAX_LIMITS
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.conformance.gate import aggregate_gate
from speclino.domain import AnalysisLimits, CodeIR
from speclino.spec import load_bindings, load_domain, load_spec_ir

REPO = Path(__file__).resolve().parents[2]
ORDERS = REPO / "examples" / "orders"
DEMOS = REPO / "demos" / "cancel_order"
DOMAIN = load_domain(ORDERS / ".speclino" / "domain.yaml")
BINDINGS = load_bindings(ORDERS / ".speclino" / "bindings.yaml")
SPEC = load_spec_ir(ORDERS / "specs" / "ir" / "cancel_order.spec-ir.json")


def analyze(tmp_path: Path, demo: str, limits: AnalysisLimits = DEFAULT_LIMITS) -> CodeIR:
    """examples/ を写し、service.py をデモの変異で差し替えて解析する。"""
    shutil.copytree(REPO / "examples", tmp_path / "examples")
    shutil.copy(DEMOS / demo, tmp_path / "examples" / "orders" / "app" / "service.py")
    return analyze_repo(tmp_path, "cancel_order", DOMAIN, BINDINGS, limits=limits)


def verdicts(code: CodeIR) -> dict[str, tuple[str, str]]:
    return {
        f.requirement_id: (f.verdict, f.category) for f in evaluate(SPEC, code, DOMAIN).findings
    }


def gate_status(code: CodeIR) -> str:
    findings = evaluate(SPEC, code, DOMAIN)
    return aggregate_gate(findings, {r.id: r.level for r in SPEC.rules}).status


def test_demo1_admin_only_is_a_violation_with_counterexample(tmp_path: Path) -> None:
    """デモ①: 在庫復元を管理者経路だけに移す → 反例つきの VIOLATED。"""
    code = analyze(tmp_path, "demo1_admin_only.py")
    findings = evaluate(SPEC, code, DOMAIN)
    f = next(f for f in findings.findings if f.requirement_id == "R-CANCEL-04")
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")
    assert f.counterexample is not None and f.counterexample.kind == "symbolic"
    # 反例は「非管理者・所有者・PENDING」
    assert "not(before.actor.is_admin)" in f.counterexample.description
    assert gate_status(code) == "fail"
    # 他の 8 要求は巻き込まない
    assert sum(1 for v in verdicts(code).values() if v[0] == "VERIFIED_IN_SCOPE") == 8


def test_demo2_deep_helpers_need_agent_escalation(tmp_path: Path) -> None:
    """デモ②: 既定の深さ上限では UNKNOWN、analyze_focus の上限引上げで VIOLATED に確定する。"""
    code = analyze(tmp_path / "default", "demo2_deep_helpers.py")
    assert verdicts(code)["R-CANCEL-04"] == ("UNKNOWN", "unsupported_analysis")
    assert {u.code for p in code.paths for u in p.unknown_causes} == {"limit_depth"}
    assert gate_status(code) == "fail"

    deeper = analyze(tmp_path / "focus", "demo2_deep_helpers.py", MAX_LIMITS)
    assert verdicts(deeper)["R-CANCEL-04"] == ("VIOLATED", "missing_effect")
    assert all(not p.unknown_causes for p in deeper.paths)


def test_demo5_loop_fails_closed_without_dragging_other_rules(tmp_path: Path) -> None:
    """デモ⑤: 対応外のループは UNKNOWN でゲート失敗。触れない観測の要求は判定できる（S29）。"""
    code = analyze(tmp_path, "demo5_loop.py")
    v = verdicts(code)
    assert v["R-CANCEL-04"] == ("UNKNOWN", "unsupported_analysis")
    assert gate_status(code) == "fail"
    for rid, val in v.items():
        if rid != "R-CANCEL-04":
            assert val == ("VERIFIED_IN_SCOPE", "conformant"), (rid, val)
    unknown = next(f for f in evaluate(SPEC, code, DOMAIN).findings if f.verdict == "UNKNOWN")
    assert unknown.next_steps, "未検証には次にできることを添える"


@pytest.mark.parametrize("demo", ["demo1_admin_only.py", "demo2_deep_helpers.py", "demo5_loop.py"])
def test_demo_sources_stay_in_the_supported_subset_entrypoint(demo: str) -> None:
    """デモの変異は examples/orders の入口シンボルを保つ（bindings を変えずに差し替えられる）。"""
    src = (DEMOS / demo).read_text(encoding="utf-8")
    assert "def cancel_order(user: User, order: Order) -> Response:" in src


def test_readme_diagrams_match_design_sources() -> None:
    """README の mermaid ブロックと `design/*.mmd`（`make diagrams` の入力）が食い違わない。"""
    import re

    blocks = [
        b.strip()
        for b in re.findall(
            r"```mermaid\n(.*?)```", (REPO / "README.md").read_text(encoding="utf-8"), re.S
        )
    ]
    for name in ("architecture", "pipeline"):
        src = (REPO / "design" / f"{name}.mmd").read_text(encoding="utf-8").strip()
        assert blocks.count(src) == 1, (
            f"{name}: README に design/ と一致するブロックが 1 つだけ無い（make diagrams）"
        )
