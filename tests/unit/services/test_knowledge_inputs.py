"""S65: bundle → 検査器への入力（services/knowledge_inputs.py）。"""

from __future__ import annotations

from datetime import UTC, datetime

from speclino.domain.bindings import BehaviorBinding, Bindings, OperationBinding
from speclino.domain.knowledge import Bundle
from speclino.services.knowledge_inputs import OperationResolver, bundle_hash


def _op(symbol: str) -> OperationBinding:
    return OperationBinding(symbol=symbol, claim_scope="command_invocation")


BINDINGS = Bindings(
    behaviors={
        "checkout": BehaviorBinding(entrypoint="shop.service.OrderService.checkout"),
        "cancel_order": BehaviorBinding(entrypoint="shop.service.cancel_order"),
        "OrderService.cancel": BehaviorBinding(entrypoint="shop.service.cancel_order"),
    },
    operations={
        "inventory_reserve": _op("shop.inventory.InventoryService.reserve"),
        "inventory_restore": _op("shop.inventory.restore"),
        # 図の名前をキーにした上書き（同じシンボルの別名）
        "InventoryService.restore": _op("shop.inventory.restore"),
        "a_charge": _op("shop.pay.PaymentGateway.charge"),
        "b_charge": _op("other.PaymentGateway.charge"),
    },
)
R = OperationResolver(BINDINGS)


def test_operation_name_match_by_symbol_tail() -> None:
    assert R.operation("InventoryService.reserve") == "inventory_reserve"


def test_operation_override_resolves_to_the_analyzers_id() -> None:
    # 上書きのキーは同じシンボルの別名。Code IR のイベントの ID（先に書かれた方）にそろえる
    assert R.operation("InventoryService.restore") == "inventory_restore"


def test_operation_ambiguous_or_missing_is_none() -> None:
    assert R.operation("PaymentGateway.charge") is None
    assert R.operation("Nothing.here") is None
    # 末尾の一部だけ（クラス名の途中）では一致しない
    assert R.operation("Service.reserve") is None


def test_behavior_by_key_then_entrypoint_tail() -> None:
    assert R.behavior("OrderService.checkout") == "checkout"
    assert R.behavior("OrderService.cancel") == "OrderService.cancel"
    assert R.behavior("OrderService.refund") is None


def test_known_operations_include_bindings() -> None:
    assert R.known_operations([]) == frozenset(BINDINGS.operations)


def test_bundle_hash_covers_signature() -> None:
    b = Bundle(
        workspace="w",
        generation=1,
        scope_hash="sha256:" + "0" * 64,
        signature="hmac-sha256:aa",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    h = bundle_hash(b)
    assert h.startswith("sha256:") and h == bundle_hash(b)
    assert bundle_hash(b.model_copy(update={"signature": "hmac-sha256:bb"})) != h
