from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.services.check_pipeline import (
    CheckInputError,
    CheckRequest,
    run_check,
    write_result,
)
from tests.fixtures import cancel_order as co

REPO = Path(__file__).resolve().parents[3]
ORDERS = REPO / "examples" / "orders"


def orders_request(**kw: object) -> CheckRequest:
    base = {
        "repo_root": REPO,
        "head_sha": "WORKTREE",
        "project_dir": ORDERS,
        "limits": DEFAULT_LIMITS,
        "source_roots": (".",),
    }
    base.update(kw)
    return CheckRequest(**base)  # type: ignore[arg-type]


def test_examples_orders_passes_with_manifest_hashes() -> None:
    res = run_check(orders_request(run_id="run-1"))
    assert res.findings.counts.VERIFIED_IN_SCOPE == 9 and res.gate.status == "pass"
    inp = res.manifest.inputs
    assert inp.spec_ir_hash and inp.bindings_hash and inp.domain_hash and inp.contracts_hash
    assert inp.spec_content_hash is not None  # specs/orders-cancel.md が見つかる
    assert res.manifest.versions.engine and res.manifest.versions.solver
    assert [r.spec.behavior_id for r in res.validation] == ["cancel_order"]
    assert res.policy_findings == []  # examples/orders に規約パッケージは無い


def test_code_ir_override_and_fail_on(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    res = run_check(orders_request(code_ir_override=override, fail_on=("VIOLATED",), run_id="r"))
    f = next(f for f in res.findings.findings if f.requirement_id == "R-CANCEL-04")
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")
    assert res.gate.status == "fail"
    assert res.manifest.inputs.code_ir_hash is not None
    assert res.code_irs[0].origin == "handwritten"


def test_override_with_unknown_behavior_is_input_error(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(
        co.RESTORE_ADMIN_ONLY.model_copy(update={"behavior_id": "other"}).model_dump_json()
    )
    with pytest.raises(CheckInputError):
        run_check(orders_request(code_ir_override=override))


def test_validation_error_is_input_error(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    shutil.copytree(ORDERS / ".speclino", project / ".speclino")
    shutil.copytree(ORDERS / "specs", project / "specs")
    ir_path = project / "specs" / "ir" / "cancel_order.spec-ir.json"
    data = json.loads(ir_path.read_text())
    data["rules"][0]["applicability"] = {"op": "ref", "path": "before.order.color"}
    ir_path.write_text(json.dumps(data))
    with pytest.raises(CheckInputError, match="E_REF_UNDEFINED"):
        run_check(orders_request(project_dir=project))


def test_self_repository_check_passes_including_policies() -> None:
    res = run_check(CheckRequest(repo_root=REPO, head_sha="WORKTREE", run_id="self"))
    ids = {f.requirement_id for f in res.findings.findings}
    assert {"R-TOOL-01", "R-TOOL-02", "MAKE-001"} <= ids
    assert any(i.startswith("SPEC-FMT-001:") for i in ids)
    assert res.gate.status == "pass", [(f.requirement_id, f.verdict) for f in res.gate.blocking]


def test_artifacts_are_deterministic_modulo_run_metadata(tmp_path: Path) -> None:
    a = write_result(run_check(orders_request(run_id="fixed")), tmp_path / "a")
    b = write_result(run_check(orders_request(run_id="fixed")), tmp_path / "b")
    for name in ("findings.json", "gate.json", "spec-ir.json", "code-ir.json"):
        assert a[name].read_text() == b[name].read_text(), name
    ma, mb = (
        json.loads(a["run-manifest.json"].read_text()),
        json.loads(b["run-manifest.json"].read_text()),
    )
    ma.pop("created_at"), mb.pop("created_at")
    assert ma == mb
