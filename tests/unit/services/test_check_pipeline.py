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


@pytest.mark.selfcheck
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


# ---------------------------------------------------------------- S28 semantic diff


def _orders_copy(dst: Path, *, mutate: bool) -> Path:
    """examples/orders を dst/examples/orders に複製する（mutate: 在庫復元を管理者経路だけに）。"""
    root = dst / "examples" / "orders"
    for name in ("app", "specs", ".speclino"):
        shutil.copytree(ORDERS / name, root / name)
    (dst / "examples" / "__init__.py").write_text("")
    (root / "__init__.py").write_text("")
    if mutate:
        svc = root / "app" / "service.py"
        src = svc.read_text()
        assert "    inventory.restore(order.items)\n" in src
        svc.write_text(
            src.replace(
                "    inventory.restore(order.items)\n",
                "    if user.is_admin:\n        inventory.restore(order.items)\n",
            )
        )
    return root


def test_base_root_yields_semantic_diff_and_annotates_findings(tmp_path: Path) -> None:
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=True)
    res = run_check(
        CheckRequest(
            repo_root=tmp_path / "head",
            head_sha="head-sha",
            base_sha="base-sha",
            project_dir=head,
            base_root=tmp_path / "base",
            base_project_dir=base,
            run_id="diff",
        )
    )
    diff = res.semantic_diff
    assert diff is not None and diff.base_available
    assert (diff.base_commit, diff.head_commit) == ("base-sha", "head-sha")
    assert diff.counts.new_violation == 1 and diff.counts.unchanged == 8
    delta = next(d for d in diff.findings if d.requirement_id == "R-CANCEL-04")
    assert (delta.base_verdict, delta.head_verdict) == ("VERIFIED_IN_SCOPE", "VIOLATED")
    assert all(r.status == "unchanged" for r in diff.rules)
    [beh] = diff.behaviors
    assert beh.status == "changed" and len(beh.added_paths) == 2 and len(beh.removed_paths) == 1
    by = {f.requirement_id: f.base_head_status for f in res.findings.findings}
    assert by["R-CANCEL-04"] == "new" and by["R-CANCEL-01"] == "unchanged"
    out = write_result(res, tmp_path / "out")
    assert "semantic-diff.json" in out
    assert "新規違反 | 1" in out["review.md"].read_text(encoding="utf-8")


def test_base_without_spec_is_reported_not_fatal(tmp_path: Path) -> None:
    head = _orders_copy(tmp_path / "head", mutate=False)
    (tmp_path / "base").mkdir()  # 初回導入: base に .speclino も specs も無い
    res = run_check(
        CheckRequest(
            repo_root=tmp_path / "head",
            head_sha="h",
            base_sha="b",
            project_dir=head,
            base_root=tmp_path / "base",
            base_project_dir=tmp_path / "base",
            run_id="first",
        )
    )
    assert res.gate.status == "pass"
    assert res.semantic_diff is not None and not res.semantic_diff.base_available
    assert "domain.yaml が無い" in (res.semantic_diff.base_error or "")
    assert {d.status for d in res.semantic_diff.findings} == {"added"}


# ---------------------------------------------------------------- S41c 増分解析


def test_incremental_reuses_when_base_and_head_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=False)
    req = CheckRequest(
        repo_root=tmp_path / "head",
        head_sha="head-sha",
        base_sha="base-sha",
        project_dir=head,
        base_root=tmp_path / "base",
        base_project_dir=base,
        run_id="fixed",
    )

    with_reuse = run_check(req)
    [analysis] = with_reuse.manifest.analysis
    assert analysis.behavior_id == "cancel_order"
    assert analysis.reused_from_base is True
    assert analysis.reason == "reachable と inputs が base と一致"
    assert analysis.reachability.functions  # 到達関数が manifest に残る（隠さない）

    # 「reused を強制オフにした結果と比較」: _decide_reuse だけを常に不再利用にして比較する
    # （base_root 自体は付けたままにして、意味差分・base_head_status 付与は両方で同じにする）
    import speclino.services.check_pipeline as check_pipeline_module

    monkeypatch.setattr(
        check_pipeline_module, "_decide_reuse", lambda *a, **k: (False, "forced-off-for-test")
    )
    without_reuse = run_check(req)
    assert without_reuse.manifest.analysis[0].reused_from_base is False

    # 決定性: 再利用の有無で findings が完全一致する（finding id を含む）
    assert with_reuse.findings.model_dump(mode="json") == without_reuse.findings.model_dump(
        mode="json"
    )


def test_incremental_reanalyzes_when_reachable_function_body_changes(tmp_path: Path) -> None:
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=True)  # service.py の cancel_order を変更

    res = run_check(
        CheckRequest(
            repo_root=tmp_path / "head",
            head_sha="head-sha",
            base_sha="base-sha",
            project_dir=head,
            base_root=tmp_path / "base",
            base_project_dir=base,
            run_id="fixed",
        )
    )
    [analysis] = res.manifest.analysis
    assert analysis.reused_from_base is False
    assert analysis.reason == "reachable ハッシュが base と不一致"


def test_incremental_does_not_reuse_when_spec_ir_changes(tmp_path: Path) -> None:
    """コードは同じでも仕様（Spec IR の rules）が変われば inputs_hash が変わり再利用しない。"""
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=False)
    ir_path = head / "specs" / "ir" / "cancel_order.spec-ir.json"
    data = json.loads(ir_path.read_text())
    data["rules"][0]["title"] = "仕様側だけの変更（テスト用）"
    ir_path.write_text(json.dumps(data))

    res = run_check(
        CheckRequest(
            repo_root=tmp_path / "head",
            head_sha="head-sha",
            base_sha="base-sha",
            project_dir=head,
            base_root=tmp_path / "base",
            base_project_dir=base,
            run_id="fixed",
        )
    )
    [analysis] = res.manifest.analysis
    assert analysis.reused_from_base is False
    assert analysis.reason == "inputs ハッシュが base と不一致"


def test_incremental_does_not_reuse_with_code_ir_override(tmp_path: Path) -> None:
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=False)
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())

    res = run_check(
        CheckRequest(
            repo_root=tmp_path / "head",
            head_sha="head-sha",
            base_sha="base-sha",
            project_dir=head,
            base_root=tmp_path / "base",
            base_project_dir=base,
            code_ir_override=override,
            run_id="fixed",
        )
    )
    [analysis] = res.manifest.analysis
    assert analysis.reused_from_base is False
    assert analysis.reason == "override"
    assert res.code_irs[0].origin == "handwritten"
