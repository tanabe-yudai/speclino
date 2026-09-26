from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.domain.policy import (
    PolicyApplicability,
    PolicyPackage,
    PolicyRevision,
    PolicyRule,
    PolicyVerifierRef,
)
from speclino.services.check_pipeline import (
    CheckInputError,
    CheckRequest,
    _run_policies,
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
    # 例外（.speclino/exceptions.yaml）で免除された VIOLATED があると pass_with_exceptions に
    # なる。免除は適合に書き換えず別記する設計（AC-009）なので、どちらも「通過」として扱う。
    # 落としてよいのは必須ルールの非成立が残る場合だけ。
    assert res.gate.status in ("pass", "pass_with_exceptions"), [
        (f.requirement_id, f.verdict) for f in res.gate.blocking
    ]
    assert not res.gate.blocking


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


def test_incremental_reanalyzes_when_only_an_import_binding_changes(tmp_path: Path) -> None:
    """import の束縛だけをすり替えても、関数本体のテキストは1文字も変わらない。

    `Response` を `from fastapi import Response` から `from builtins import print as Response`
    へ変えると、`cancel_order` の関数本体（`ast.unparse`）は完全に同一のまま、解析器が
    `Response(status_code=204)` を FastAPI の応答として認識できなくなり（R-CANCEL-03〜06 が
    UNKNOWN になる）、意味は変わる。S41c 最初の実装は関数本体だけをハッシュにしていたため
    これを見落とし、base 付きで誤って全要求 VERIFIED のまま再利用していた（bugfix: モジュール
    全体をハッシュに含めるようにした）。head 単独解析（base なし、常に再解析）と findings の
    verdict が一致することで、誤った再利用が起きていないことを確認する。
    """
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=False)
    svc = head / "app" / "service.py"
    src = svc.read_text()
    assert "from fastapi import HTTPException, Response\n" in src
    svc.write_text(
        src.replace(
            "from fastapi import HTTPException, Response\n",
            "from fastapi import HTTPException\nfrom builtins import print as Response\n",
        )
    )

    with_base = run_check(
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
    [analysis] = with_base.manifest.analysis
    assert analysis.reused_from_base is False
    assert analysis.reason == "reachable ハッシュが base と不一致"

    head_only = run_check(
        CheckRequest(
            repo_root=tmp_path / "head", head_sha="head-sha", project_dir=head, run_id="fixed"
        )
    )
    verdicts_with_base = {f.requirement_id: f.verdict for f in with_base.findings.findings}
    verdicts_head_only = {f.requirement_id: f.verdict for f in head_only.findings.findings}
    assert verdicts_with_base == verdicts_head_only
    # 誤って再利用していれば base の（正しい import のままの）Code IR がそのまま使われ、
    # 全要求 VERIFIED になってしまう。実際には応答判定ができず UNKNOWN が残るはず
    assert "UNKNOWN" in verdicts_with_base.values()


def test_incremental_reanalyzes_when_an_imported_module_changes(tmp_path: Path) -> None:
    """呼出しグラフに現れない import 先（列挙型の定義）が変わっても再利用しない。

    `service.py` は1文字も変えず、`app/models.py` の `OrderStatus.PENDING` を `WAITING`
    へ改名するだけ。`cancel_order` はどの関数も呼び出し先を変えていないので、旧実装（関数の
    呼出しグラフだけを到達性とする実装）は到達ハッシュを一致させ、base の
    （`PENDING` が解決できていた頃の）VERIFIED_IN_SCOPE をそのまま再利用してしまっていた。
    実際には `order.status != OrderStatus.PENDING` の属性参照が解決できなくなり、
    R-CANCEL-02 に依存する要求が UNKNOWN になる（bugfix: import の推移閉包をたどる）。
    """
    base = _orders_copy(tmp_path / "base", mutate=False)
    head = _orders_copy(tmp_path / "head", mutate=False)
    models = head / "app" / "models.py"
    src = models.read_text()
    assert '    PENDING = "PENDING"\n' in src
    models.write_text(src.replace('    PENDING = "PENDING"\n', '    WAITING = "PENDING"\n'))

    with_base = run_check(
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
    [analysis] = with_base.manifest.analysis
    assert analysis.reused_from_base is False
    assert "examples.orders.app.models" in analysis.reachability.modules

    head_only = run_check(
        CheckRequest(
            repo_root=tmp_path / "head", head_sha="head-sha", project_dir=head, run_id="fixed"
        )
    )
    verdicts_with_base = {f.requirement_id: f.verdict for f in with_base.findings.findings}
    verdicts_head_only = {f.requirement_id: f.verdict for f in head_only.findings.findings}
    assert verdicts_with_base == verdicts_head_only
    assert "UNKNOWN" in verdicts_with_base.values()


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


# ---------------------------------------------------------------- S42 チーム規約


def mandatory_rule(
    id: str, paths: list[str], *, verifier_id: str = "makefile.required_targets"
) -> PolicyRule:
    """`applies_to` に一致する対象が無い前提のルール（missing_input 経路だけを使う）。"""
    return PolicyRule(
        id=id,
        version="1.0.0",
        level="mandatory",
        title=id,
        text=id,
        applies_to=PolicyApplicability(paths=paths, languages=[]),
        verifier=PolicyVerifierRef(id=verifier_id, version="1.0.0", params={}),
        on_missing_input="unknown",
        exceptions_allowed=True,
    )


def advisory_rule_without_verifier(id: str, paths: list[str]) -> PolicyRule:
    return PolicyRule(
        id=id,
        version="1.0.0",
        level="advisory",
        title=id,
        text=id,
        applies_to=PolicyApplicability(paths=paths, languages=[]),
        verifier=None,
        on_missing_input="violation",
        exceptions_allowed=True,
    )


def team_revision(
    rules: list[PolicyRule], *, team: str = "acme", revision: int = 1
) -> PolicyRevision:
    package = PolicyPackage(
        schema_version="0.1",
        id="team-pkg",
        version="1.0.0",
        status="active",
        owner="team",
        rules=rules,
    )
    return PolicyRevision(
        team=team,
        revision=revision,
        status="active",
        package=package,
        author="alice",
        approver="bob",
        note="test",
    )


def test_team_policy_runs_without_project_speclino_policy_dir() -> None:
    """examples/orders には .speclino/policy/ が無いが、team_policy だけで検査が走る。"""
    revision = team_revision([mandatory_rule("TEAM-001", ["team-only/*.never"])])
    res = run_check(orders_request(run_id="r-team-only", team_policy=revision))
    by_id = {f.requirement_id: f for f in res.policy_findings}
    assert by_id["TEAM-001"].verdict == "UNKNOWN"
    assert res.manifest.policy_team == "acme"
    assert res.manifest.policy_revision == 1


def test_manifest_has_no_policy_revision_without_team_policy() -> None:
    res = run_check(orders_request(run_id="r-no-team-policy"))
    assert res.manifest.policy_team is None
    assert res.manifest.policy_revision is None


def test_team_policy_advisory_rule_without_verifier_makes_no_finding() -> None:
    """検証器の無いルール（助言のみ）は根拠の無い verdict を作らない。"""
    revision = team_revision([advisory_rule_without_verifier("TEAM-ADV-001", ["specs/*.md"])])
    res = run_check(orders_request(run_id="r-advisory", team_policy=revision))
    assert not any(f.requirement_id == "TEAM-ADV-001" for f in res.policy_findings)


_PROJECT_POLICY_YAML = """\
schema_version: "0.1"
id: proj-extra
version: "1.0.0"
status: active
owner: tester
rules:
  - id: TEAM-001
    version: "1.0.0"
    level: mandatory
    title: overridden by team policy
    text: この定義はチーム規約に上書きされるはず
    applies_to:
      paths: ["specs/*.md"]
      languages: []
    verifier:
      id: does.not.exist
      version: "1.0.0"
      params: {}
    on_missing_input: violation
    exceptions_allowed: true
  - id: PROJ-001
    version: "1.0.0"
    level: mandatory
    title: project only
    text: プロジェクト固有の追加規約
    applies_to:
      paths: ["team-only/*.never"]
      languages: []
    verifier:
      id: makefile.required_targets
      version: "1.0.0"
      params: {}
    on_missing_input: unknown
    exceptions_allowed: true
"""


def _project_with_local_policy(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    shutil.copytree(ORDERS / ".speclino", project / ".speclino")
    shutil.copytree(ORDERS / "specs", project / "specs")
    policy_dir = project / ".speclino" / "policy"
    policy_dir.mkdir()
    (policy_dir / "local.yaml").write_text(_PROJECT_POLICY_YAML, encoding="utf-8")
    return project


def test_team_policy_takes_precedence_over_same_id_project_rule(tmp_path: Path) -> None:
    """同じ rule id が両方にあれば、チーム規約を優先しプロジェクト側は無視する。

    プロジェクト側の TEAM-001 は未登録の検証器 `does.not.exist` を参照している。無視されずに
    実行されれば `VerifierRegistry` が LookupError を投げるので、例外が出ないこと自体が
    「プロジェクト側が実行されていない」ことの証拠になる。
    """
    project = _project_with_local_policy(tmp_path)
    # 一致する対象（specs/*.md）を用意し、チーム側の TEAM-001 が実際に評価されることを確認する
    revision = team_revision(
        [mandatory_rule("TEAM-001", ["specs/*.md"], verifier_id="makefile.required_targets")]
    )

    res = run_check(orders_request(project_dir=project, team_policy=revision, run_id="r-both"))

    ids = [f.requirement_id for f in res.policy_findings]
    assert ids.count("TEAM-001") == 1  # プロジェクト側の重複が無い
    assert "PROJ-001" in ids  # プロジェクト固有の別ルールは実行される

    findings, levels, texts = _run_policies(project, revision)
    assert any("TEAM-001" in t and "無視" in t for t in texts), texts
    assert [f.requirement_id for f in findings].count("TEAM-001") == 1
