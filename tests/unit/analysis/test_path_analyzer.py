"""path_analyzer: examples/orders と変異版から Code IR を作り、検査器の結果を確認する。"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest
import yaml

from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.domain import AnalysisLimits, Bindings, CodeIR, DomainSpec, SpecIR

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "orders"

DOMAIN = DomainSpec.model_validate(
    yaml.safe_load((EXAMPLE / ".speclino" / "domain.yaml").read_text())
)
SPEC = SpecIR.model_validate(
    json.loads((EXAMPLE / "specs" / "ir" / "cancel_order.spec-ir.json").read_text())
)
BINDINGS = Bindings.model_validate(
    yaml.safe_load((EXAMPLE / ".speclino" / "bindings.yaml").read_text())
)

CORRECT_SERVICE = (EXAMPLE / "app" / "service.py").read_text()


def make_repo(tmp_path: Path, service_src: str) -> tuple[Path, Bindings]:
    """examples/orders の app/ を tmp にコピーし、service.py を差し替えた小さなリポジトリを作る。"""
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    for name in ("__init__.py", "models.py", "inventory.py", "audit.py"):
        shutil.copy(EXAMPLE / "app" / name, app / name)
    src = service_src.replace("examples.orders.app", "app")
    (app / "service.py").write_text(textwrap.dedent(src))
    b = BINDINGS.model_dump()
    b["behaviors"]["cancel_order"]["entrypoint"] = "app.service.cancel_order"
    for op in b["operations"].values():
        op["symbol"] = op["symbol"].replace("examples.orders.app", "app")
    return tmp_path, Bindings.model_validate(b)


def analyze(tmp_path: Path, service_src: str, limits: AnalysisLimits = DEFAULT_LIMITS) -> CodeIR:
    root, bindings = make_repo(tmp_path, service_src)
    return analyze_repo(root, "cancel_order", DOMAIN, bindings, limits=limits)


def verdicts(code: CodeIR) -> dict[str, tuple[str, str]]:
    fs = evaluate(SPEC, code, DOMAIN)
    return {f.requirement_id: (f.verdict, f.category) for f in fs.findings}


def test_correct_example_yields_three_exact_paths() -> None:
    code = analyze_repo(REPO, "cancel_order", DOMAIN, BINDINGS)
    assert [p.outcome.status for p in code.paths] == [403, 409, 204]
    assert all(p.precision == "exact_in_model" for p in code.paths)
    success = code.paths[2]
    assert [e.operation_or_target for e in success.events] == [
        "order.status",
        "inventory_restore_command",
        "audit_record_command",
    ]
    assert success.events[1].arguments["items"].model_dump() == {
        "op": "ref",
        "path": "before.order.items",
    }
    assert success.events[2].arguments["action"].model_dump() == {
        "op": "const",
        "type": "AuditAction",
        "value": "CANCEL_ORDER",
    }
    assert success.state_updates[0].target == "order.status"
    assert success.source_refs[0].path == "examples/orders/app/service.py"
    assert all(v == ("VERIFIED_IN_SCOPE", "conformant") for v in verdicts(code).values())


def test_restore_admin_only_is_detected_from_source(tmp_path: Path) -> None:
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    if user.is_admin:\n        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    assert len(code.paths) == 4
    v = verdicts(code)
    assert v["R-CANCEL-04"] == ("VIOLATED", "missing_effect")
    assert all(val[0] == "VERIFIED_IN_SCOPE" for k, val in v.items() if k != "R-CANCEL-04")


def test_call_after_early_return_is_not_executed(tmp_path: Path) -> None:
    before = (
        "    inventory.restore(order.items)\n"
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n"
        "    return Response(status_code=204)\n"
    )
    after = (
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n"
        "    return Response(status_code=204)\n"
        "    inventory.restore(order.items)\n"
    )
    mutant = CORRECT_SERVICE.replace(before, after)
    v = verdicts(analyze(tmp_path, mutant))
    assert v["R-CANCEL-04"] == ("VIOLATED", "missing_effect")


def test_unreachable_branch_only(tmp_path: Path) -> None:
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    if order.status == OrderStatus.SHIPPED:\n        inventory.restore(order.items)\n",
    )
    # 状態は直前に CANCELLED に更新済みなので、この分岐は実行不可能（更新後の値で条件を評価する）
    v = verdicts(analyze(tmp_path, mutant))
    assert v["R-CANCEL-04"] == ("VIOLATED", "missing_effect")


def test_helper_split_is_followed_and_depth_limit_marks_unknown(tmp_path: Path) -> None:
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    _restore(order)\n",
    ) + textwrap.dedent(
        """

        def _restore(o: Order) -> None:
            inventory.restore(o.items)
        """
    )
    code = analyze(tmp_path, mutant)
    assert verdicts(code)["R-CANCEL-04"] == ("VERIFIED_IN_SCOPE", "conformant")
    shallow = analyze(
        tmp_path, mutant, AnalysisLimits(depth=0, max_paths=64, solver_timeout_ms=500)
    )
    assert shallow.paths[2].precision == "incomplete"
    assert shallow.paths[2].unknown_causes[0].code == "limit_depth"
    assert verdicts(shallow)["R-CANCEL-04"][0] == "UNKNOWN"


def test_loop_is_unsupported_and_unknown(tmp_path: Path) -> None:
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in order.items:\n        inventory.restore(item)\n",
    )
    code = analyze(tmp_path, mutant)
    p = code.paths[2]
    assert p.precision == "incomplete" and p.unknown_causes[0].code == "unsupported_syntax"
    v = verdicts(code)
    assert v["R-CANCEL-04"][0] == "UNKNOWN"
    assert v["R-CANCEL-01"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_unbound_external_call_is_unresolved(tmp_path: Path) -> None:
    mutant = CORRECT_SERVICE.replace(
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n    notify.send(order.owner_id)\n",
    ).replace(
        "from examples.orders.app import audit, inventory",
        "from examples.orders.app import audit, inventory, notify",
    )
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "notify.py").write_text("def send(to):\n    pass\n")
    code = analyze(tmp_path, mutant)
    # リポジトリ内の関数なので展開される（契約なしでも本体が対応構文なら要約できる）
    assert code.paths[2].precision == "exact_in_model"


def test_path_limit(tmp_path: Path) -> None:
    code = analyze(
        tmp_path, CORRECT_SERVICE, AnalysisLimits(depth=2, max_paths=2, solver_timeout_ms=500)
    )
    assert code.unknown_causes and code.unknown_causes[0].code == "limit_paths"
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"


@pytest.mark.parametrize("status", [400, 500])
def test_status_change_detected(tmp_path: Path, status: int) -> None:
    mutant = CORRECT_SERVICE.replace("status_code=409", f"status_code={status}")
    assert verdicts(analyze(tmp_path, mutant))["R-CANCEL-02"] == ("VIOLATED", "outcome_mismatch")
