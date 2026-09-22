"""自己検証: SpecLino の ToolGateway.invoke を SPEC-TOOL-001 の Spec IR で検査する（S24）。"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.spec import load_bindings, load_domain, load_spec_ir, validate_spec_ir

REPO = Path(__file__).resolve().parents[2]
DOMAIN = load_domain(REPO / ".speclino" / "domain.yaml")
BINDINGS = load_bindings(REPO / ".speclino" / "bindings.yaml")
SPEC = load_spec_ir(REPO / "specs" / "ir" / "tool_gateway.spec-ir.json")
GATEWAY = REPO / "src" / "speclino" / "agent" / "gateway.py"


def verdicts(root: Path) -> dict[str, tuple[str, str]]:
    code = analyze_repo(root, "tool_gateway_invoke", DOMAIN, BINDINGS)
    assert code.paths, code.unknown_causes
    return {
        f.requirement_id: (f.verdict, f.category) for f in evaluate(SPEC, code, DOMAIN).findings
    }


def test_spec_ir_is_valid_and_approved_against_current_markdown() -> None:
    md = (REPO / "specs" / "SPEC-TOOL-001.md").read_text(encoding="utf-8")
    report = validate_spec_ir(SPEC, DOMAIN, BINDINGS, md)
    assert report.ok, [d.model_dump() for d in report.errors]
    assert not report.warnings, [d.model_dump() for d in report.warnings]


@pytest.mark.spec("R-TOOL-01")
@pytest.mark.spec("R-TOOL-02")
def test_gateway_invoke_satisfies_spec_tool_001() -> None:
    code = analyze_repo(REPO, "tool_gateway_invoke", DOMAIN, BINDINGS)
    assert [p.outcome.kind for p in code.paths] == ["return", "return", "return"]
    assert all(p.precision == "exact_in_model" for p in code.paths), [
        (p.path_id, [u.detail for u in p.unknown_causes]) for p in code.paths
    ]
    v = verdicts(REPO)
    assert all(val == ("VERIFIED_IN_SCOPE", "conformant") for val in v.values()), v


def _mutant_repo(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    pkg = tmp_path / "src" / "speclino" / "agent"
    pkg.mkdir(parents=True)
    for name in ("__init__.py", "budget.py", "permissions.py"):
        shutil.copy(GATEWAY.parent / name, pkg / name)
    (tmp_path / "src" / "speclino" / "__init__.py").write_text("")
    src = GATEWAY.read_text(encoding="utf-8")
    mutated = transform(src)
    assert mutated != src
    (pkg / "gateway.py").write_text(mutated)
    return tmp_path


def test_execute_before_permission_check_violates_r_tool_01(tmp_path: Path) -> None:
    # デモ 3: registry.execute を権限判定の前に移す
    permitted_line = "        permitted = self.permissions.allows(call.tool)\n"
    v = verdicts(
        _mutant_repo(
            tmp_path,
            lambda src: src.replace(
                permitted_line, "        result = self.registry.execute(call)\n" + permitted_line
            ),
        )
    )
    assert v["R-TOOL-01"] == ("VIOLATED", "forbidden_effect")
    assert v["R-TOOL-03"] == ("VIOLATED", "forbidden_effect")
    assert v["R-TOOL-02"][0] == "VIOLATED"  # 2 回実行される（cardinality_mismatch）


def test_missing_denied_audit_violates_r_tool_01b(tmp_path: Path) -> None:
    audit_line = (
        '            self.audit.record_denied(call, "not_permitted",'
        " self.permissions.is_honeypot(call.tool))\n"
    )
    v = verdicts(_mutant_repo(tmp_path, lambda src: src.replace(audit_line, "")))
    assert v["R-TOOL-01b"] == ("VIOLATED", "missing_effect")
    assert v["R-TOOL-01"] == ("VERIFIED_IN_SCOPE", "conformant")
