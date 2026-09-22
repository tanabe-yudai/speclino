"""examples/orders の Golden fixture が正しく組み立てられていることを確認する。

(a) 正しい実装 `cancel_order` を実際に呼び、403/409/204 と在庫復元・監査記録の呼出し引数を確認する。
(b) domain / bindings / contracts / spec-ir が IR 型で `model_validate` を通ることを確認する。
(c) spec-ir.json が schemas/spec-ir.schema.json（JSON Schema）に適合することを確認する。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi import HTTPException

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User
from examples.orders.app.service import cancel_order
from speclino.domain.bindings import Bindings, OperationContract
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.spec_ir import SpecIR

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "orders"


@pytest.fixture(autouse=True)
def _clear_recorded_calls() -> None:
    inventory.restored_calls.clear()
    audit.recorded_calls.clear()


def _make_order(status: OrderStatus, owner_id: str = "user-owner") -> Order:
    return Order(id="order-1", owner_id=owner_id, status=status, items=("sku-1", "sku-2"))


# --- (a) 正しい実装の挙動 ---


def test_cancel_order_rejects_non_admin_non_owner_with_403() -> None:
    user = User(id="user-stranger", is_admin=False)
    order = _make_order(OrderStatus.PENDING)

    with pytest.raises(HTTPException) as exc_info:
        cancel_order(user, order)

    assert exc_info.value.status_code == 403
    assert order.status == OrderStatus.PENDING
    assert inventory.restored_calls == []
    assert audit.recorded_calls == []


def test_cancel_order_rejects_non_pending_order_with_409() -> None:
    user = User(id="user-owner", is_admin=False)
    order = _make_order(OrderStatus.SHIPPED)

    with pytest.raises(HTTPException) as exc_info:
        cancel_order(user, order)

    assert exc_info.value.status_code == 409
    assert order.status == OrderStatus.SHIPPED
    assert inventory.restored_calls == []
    assert audit.recorded_calls == []


def test_cancel_order_authorization_precedes_state_check() -> None:
    """R-CANCEL-01: 認可を状態判定より優先する。非認可 + 非PENDINGでも403を返す。"""
    user = User(id="user-stranger", is_admin=False)
    order = _make_order(OrderStatus.SHIPPED)

    with pytest.raises(HTTPException) as exc_info:
        cancel_order(user, order)

    assert exc_info.value.status_code == 403


def test_cancel_order_succeeds_for_owner() -> None:
    user = User(id="user-owner", is_admin=False)
    order = _make_order(OrderStatus.PENDING, owner_id="user-owner")

    response = cancel_order(user, order)

    assert response.status_code == 204
    assert order.status == OrderStatus.CANCELLED
    assert inventory.restored_calls == [("sku-1", "sku-2")]
    assert audit.recorded_calls == [("user-owner", AuditAction.CANCEL_ORDER)]


def test_cancel_order_succeeds_for_admin() -> None:
    user = User(id="user-admin", is_admin=True)
    order = _make_order(OrderStatus.PENDING, owner_id="user-owner")

    response = cancel_order(user, order)

    assert response.status_code == 204
    assert order.status == OrderStatus.CANCELLED
    assert inventory.restored_calls == [("sku-1", "sku-2")]
    assert audit.recorded_calls == [("user-admin", AuditAction.CANCEL_ORDER)]


# --- (b) IR 型で model_validate が通ること ---


def test_domain_yaml_validates_as_domain_spec() -> None:
    data = yaml.safe_load((EXAMPLE_DIR / ".speclino" / "domain.yaml").read_text())
    domain = DomainSpec.model_validate(data)
    assert domain.enums["OrderStatus"] == ["PENDING", "SHIPPED", "CANCELLED"]
    assert domain.type_of("order.status") == "OrderStatus"


def test_bindings_yaml_validates_as_bindings() -> None:
    data = yaml.safe_load((EXAMPLE_DIR / ".speclino" / "bindings.yaml").read_text())
    bindings = Bindings.model_validate(data)
    assert bindings.behaviors["cancel_order"].entrypoint == (
        "examples.orders.app.service.cancel_order"
    )
    assert bindings.operations["inventory_restore_command"].symbol == (
        "examples.orders.app.inventory.restore"
    )


def test_contracts_validate_as_operation_contract() -> None:
    for name in ("inventory_restore_v1", "audit_record_v1"):
        data = yaml.safe_load(
            (EXAMPLE_DIR / ".speclino" / "contracts" / f"{name}.yaml").read_text()
        )
        contract = OperationContract.model_validate(data)
        assert contract.id == name
        assert contract.assumes_success is True
        assert contract.may_modify == []


def test_spec_ir_json_validates_and_has_nine_rules_with_demo_approval() -> None:
    data = json.loads((EXAMPLE_DIR / "specs" / "ir" / "cancel_order.spec-ir.json").read_text())
    spec_ir = SpecIR.model_validate(data)

    assert spec_ir.behavior_id == "cancel_order"
    assert len(spec_ir.rules) == 9
    assert {r.id for r in spec_ir.rules} == {
        "R-CANCEL-01",
        "R-CANCEL-02",
        "R-CANCEL-03",
        "R-CANCEL-04",
        "R-CANCEL-05",
        "R-CANCEL-06",
        "R-CANCEL-07a",
        "R-CANCEL-07b",
        "R-CANCEL-07c",
    }
    assert all(r.approval_ref == "demo-approval" for r in spec_ir.rules)
    assert [a.id for a in spec_ir.approvals] == ["demo-approval"]


# --- (c) JSON Schema 適合 ---


def test_spec_ir_json_matches_schema() -> None:
    schema = json.loads((REPO_ROOT / "schemas" / "spec-ir.schema.json").read_text())
    instance = json.loads((EXAMPLE_DIR / "specs" / "ir" / "cancel_order.spec-ir.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(instance)
