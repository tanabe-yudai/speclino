"""R-CANCEL-01〜07 の spec テスト（test.run の宣言済みテスト）。

base 由来の信頼側テストとして隔離環境で head の実装に対して実行される。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from examples.orders.app import audit, inventory
from examples.orders.app.models import Order, OrderStatus, User
from examples.orders.app.service import cancel_order


@pytest.fixture(autouse=True)
def _clear() -> None:
    inventory.restored_calls.clear()
    audit.recorded_calls.clear()


def owner() -> User:
    return User(id="u-owner", is_admin=False)


def pending() -> Order:
    return Order(id="o-1", owner_id="u-owner", status=OrderStatus.PENDING, items=("sku-1", "sku-2"))


@pytest.mark.spec("R-CANCEL-01")
def test_stranger_gets_403() -> None:
    with pytest.raises(HTTPException) as e:
        cancel_order(User(id="u-other", is_admin=False), pending())
    assert e.value.status_code == 403


@pytest.mark.spec("R-CANCEL-02")
def test_shipped_gets_409() -> None:
    shipped = Order(id="o-2", owner_id="u-owner", status=OrderStatus.SHIPPED, items=("sku-1",))
    with pytest.raises(HTTPException) as e:
        cancel_order(owner(), shipped)
    assert e.value.status_code == 409


@pytest.mark.spec("R-CANCEL-04")
def test_owner_cancel_restores_inventory_once() -> None:
    order = pending()
    cancel_order(owner(), order)
    assert inventory.restored_calls == [order.items]


@pytest.mark.spec("R-CANCEL-04")
def test_admin_cancel_restores_inventory_once() -> None:
    order = pending()
    cancel_order(User(id="u-admin", is_admin=True), order)
    assert inventory.restored_calls == [order.items]


@pytest.mark.spec("R-CANCEL-03")
@pytest.mark.spec("R-CANCEL-06")
def test_owner_cancel_sets_cancelled_and_204() -> None:
    order = pending()
    res = cancel_order(owner(), order)
    assert order.status == OrderStatus.CANCELLED and res.status_code == 204


@pytest.mark.spec("R-CANCEL-07")
def test_rejected_paths_have_no_side_effects() -> None:
    order = pending()
    with pytest.raises(HTTPException):
        cancel_order(User(id="u-other", is_admin=False), order)
    assert order.status == OrderStatus.PENDING and inventory.restored_calls == []
