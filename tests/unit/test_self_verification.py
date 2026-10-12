"""自己検証: SpecLino 自身の振る舞いを、凍結した旧形式の Spec IR で検査する。

仕様の正本は SpecLino（ワークスペース）にあり、リポジトリには `specs/ir` を置かない。
ここの Spec IR は検出テストの入力として凍結したもの（tests/fixtures/selfcheck/README.md）。
"""

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
GATEWAY = REPO / "src" / "speclino" / "agent" / "gateway.py"

# デモブランチが src/speclino/... を壊しても検出テストが巻き添えで壊れないよう、
# 変異させる「元」を凍結したコピー（tests/fixtures/selfcheck/README.md 参照）。
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "selfcheck"
FROZEN_GATEWAY = FIXTURES / "gateway.py"
FROZEN_SPEC = load_spec_ir(FIXTURES / "tool_gateway.spec-ir.json")


def _frozen_verdicts(root: Path) -> dict[str, tuple[str, str]]:
    """凍結した Spec IR（FROZEN_SPEC）に対する検証。"""
    code = analyze_repo(root, "tool_gateway_invoke", DOMAIN, BINDINGS)
    assert code.paths, code.unknown_causes
    return {
        f.requirement_id: (f.verdict, f.category)
        for f in evaluate(FROZEN_SPEC, code, DOMAIN).findings
    }


@pytest.mark.selfcheck
def test_frozen_spec_ir_is_valid_against_current_vocabulary() -> None:
    """凍結した Spec IR が今の domain.yaml・bindings.yaml で読める（検出テストの前提）。"""
    report = validate_spec_ir(FROZEN_SPEC, DOMAIN, BINDINGS, None)
    assert report.ok, [d.model_dump() for d in report.errors]
    assert not report.warnings, [d.model_dump() for d in report.warnings]


# `@pytest.mark.spec` は付けない: このマーカーは「その要求を**振る舞いとして**検証するテスト」
# （Spec IR の tests に宣言して test_run が隔離実行できるもの）を指す。このテストは
# SpecLino が要求を**静的に**検証できることを確かめるもので、種類が違う（SPEC-TEST-001）。
@pytest.mark.selfcheck
def test_gateway_invoke_satisfies_spec_tool_001() -> None:
    code = analyze_repo(REPO, "tool_gateway_invoke", DOMAIN, BINDINGS)
    assert [p.outcome.kind for p in code.paths] == ["return", "return", "return"]
    assert all(p.precision == "exact_in_model" for p in code.paths), [
        (p.path_id, [u.detail for u in p.unknown_causes]) for p in code.paths
    ]
    v = _frozen_verdicts(REPO)
    assert all(val == ("VERIFIED_IN_SCOPE", "conformant") for val in v.values()), v


def _mutant_repo(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    """凍結した gateway.py（FROZEN_GATEWAY）だけを変異させた最小リポジトリ。

    __init__.py / budget.py / permissions.py はデモの対象外（凍結リスト外）なので現物を写す。
    """
    pkg = tmp_path / "src" / "speclino" / "agent"
    pkg.mkdir(parents=True)
    for name in ("__init__.py", "budget.py", "permissions.py"):
        shutil.copy(GATEWAY.parent / name, pkg / name)
    (tmp_path / "src" / "speclino" / "__init__.py").write_text("")
    src = FROZEN_GATEWAY.read_text(encoding="utf-8")
    mutated = transform(src)
    assert mutated != src
    (pkg / "gateway.py").write_text(mutated)
    return tmp_path


def test_execute_before_permission_check_violates_r_tool_01(tmp_path: Path) -> None:
    # デモ 3: registry.execute を権限判定の前に移す
    permitted_line = "        permitted = self.permissions.allows(call.tool)\n"
    v = _frozen_verdicts(
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
    v = _frozen_verdicts(_mutant_repo(tmp_path, lambda src: src.replace(audit_line, "")))
    assert v["R-TOOL-01b"] == ("VIOLATED", "missing_effect")
    assert v["R-TOOL-01"] == ("VERIFIED_IN_SCOPE", "conformant")


# ------------------------- ゲート・run の鮮度・test_run の判定

# behavior → 凍結した Spec IR
SELF_SPECS = {
    "gate_decide": "gate_decide.spec-ir.json",
    "run_freshness": "run_freshness.spec-ir.json",
    "test_run_decide": "test_run_decide.spec-ir.json",
}


@pytest.mark.selfcheck
@pytest.mark.parametrize("behavior", sorted(SELF_SPECS))
def test_gate_and_run_specs_are_valid_and_verified(behavior: str) -> None:
    spec = load_spec_ir(FIXTURES / SELF_SPECS[behavior])
    report = validate_spec_ir(spec, DOMAIN, BINDINGS, None)
    assert report.ok and not report.warnings, [d.model_dump() for d in report.diagnostics]
    code = analyze_repo(REPO, behavior, DOMAIN, BINDINGS)
    assert code.paths and all(p.precision == "exact_in_model" for p in code.paths), [
        (p.path_id, [u.detail for u in p.unknown_causes]) for p in code.paths
    ]
    v = {f.requirement_id: f.verdict for f in evaluate(spec, code, DOMAIN).findings}
    assert set(v) == {r.id for r in spec.rules} and set(v.values()) == {"VERIFIED_IN_SCOPE"}, v


# rel（src/speclino/<rel>）→ 凍結コピー。_copy_module はここにあるものだけを変異元にする。
FROZEN_MODULES = {
    "conformance/gate.py": FIXTURES / "gate.py",
    "services/run_freshness.py": FIXTURES / "run_freshness.py",
    "agent/tools/test_run.py": FIXTURES / "test_run.py",
}


def _copy_module(tmp_path: Path, rel: str, transform: Callable[[str], str]) -> Path:
    """凍結した src/speclino/<rel>（FROZEN_MODULES）だけを変異させた最小リポジトリ。

    依存する domain 型（デモの対象外・凍結リスト外）は現物を写す。
    """
    for path in ("domain/__init__.py", "domain/finding.py", "domain/run.py", "domain/common.py"):
        dst = tmp_path / "src" / "speclino" / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "src" / "speclino" / path, dst)
    (tmp_path / "src" / "speclino" / "__init__.py").write_text("")
    src = FROZEN_MODULES[rel].read_text(encoding="utf-8")
    mutated = transform(src)
    assert mutated != src
    dst = tmp_path / "src" / "speclino" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    (dst.parent / "__init__.py").write_text("")
    dst.write_text(mutated)
    return tmp_path


def _self_verdicts(root: Path, behavior: str) -> dict[str, tuple[str, str]]:
    """凍結コピー（FIXTURES 配下の Spec IR）に対する検証。検出テストはこちらを使う。"""
    spec = load_spec_ir(FIXTURES / SELF_SPECS[behavior])
    code = analyze_repo(root, behavior, DOMAIN, BINDINGS)
    assert code.paths, code.unknown_causes
    return {
        f.requirement_id: (f.verdict, f.category) for f in evaluate(spec, code, DOMAIN).findings
    }


def test_passing_without_mandatory_rules_violates_r_gate_01(tmp_path: Path) -> None:
    root = _copy_module(
        tmp_path,
        "conformance/gate.py",
        lambda s: s.replace(
            '        return _fail(t, "必須ルールが 1 つも無い")\n', "        return _pass(t)\n"
        ),
    )
    v = _self_verdicts(root, "gate_decide")
    assert v["R-GATE-01"] == ("VIOLATED", "missing_effect")
    assert v["R-GATE-01b"] == ("VIOLATED", "forbidden_effect")
    assert v["R-GATE-02"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_publishing_superseded_run_violates_r_run_01(tmp_path: Path) -> None:
    root = _copy_module(
        tmp_path,
        "services/run_freshness.py",
        lambda s: s.replace(
            '    if exec_state == "superseded":\n        return _mark_superseded()\n', ""
        ),
    )
    v = _self_verdicts(root, "run_freshness")
    assert v["R-RUN-01b"] == ("VIOLATED", "missing_effect")
    assert v["R-RUN-01"][0] == "VIOLATED"  # superseded でも is_latest なら publish される
    assert v["R-RUN-03"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_passing_test_overwrites_verdict_violates_r_test_02(tmp_path: Path) -> None:
    # S50 が直した元の不具合の再現: 宣言済みテストが全て成功したときに observed ではなく
    # violated を返す（成功の観測が判定を上書きしてしまう）。
    root = _copy_module(
        tmp_path,
        "agent/tools/test_run.py",
        lambda s: s.replace("    return _observed()\n", "    return _violated()\n"),
    )
    v = _self_verdicts(root, "test_run_decide")
    assert v["R-TEST-02"] == ("VIOLATED", "missing_effect")
    assert v["R-TEST-01"] == ("VERIFIED_IN_SCOPE", "conformant")
    assert v["R-TEST-03"] == ("VERIFIED_IN_SCOPE", "conformant")


# ---------------------------------------------------------------- fixture drift

# 現物（live）相対パス → 凍結コピー。上の検出テストが正しい「元」を変異させ続けているかを見張る。
FROZEN_TARGETS = {
    "src/speclino/agent/gateway.py": FROZEN_GATEWAY,
    "src/speclino/conformance/gate.py": FROZEN_MODULES["conformance/gate.py"],
    "src/speclino/services/run_freshness.py": FROZEN_MODULES["services/run_freshness.py"],
    "src/speclino/agent/tools/test_run.py": FROZEN_MODULES["agent/tools/test_run.py"],
}


@pytest.mark.selfcheck
def test_selfcheck_fixtures_match_live_source() -> None:
    """tests/fixtures/selfcheck の凍結コピーが現物とドリフトしていないかを確かめる。

    上の検出テスト（test_execute_before_permission_check_violates_r_tool_01 など）は
    現物ではなくこの凍結コピーを変異させる元にしている。現物を正当な理由で変更したときは
    コピーが古くなるので、ここで検知する（selfcheck マーカーなので test (backend) には無関係）。
    デモブランチで現物を意図的に壊した場合もここは落ちるが、それは想定どおりの動作。
    """
    stale = [
        rel
        for rel, frozen in FROZEN_TARGETS.items()
        if (REPO / rel).read_text(encoding="utf-8") != frozen.read_text(encoding="utf-8")
    ]
    assert not stale, (
        "fixture が古い。意図した変更なら tests/fixtures/selfcheck/ を現物からコピーし直す: "
        + ", ".join(stale)
    )
