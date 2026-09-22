"""デモ 2 用: 在庫復元をヘルパー 3 段の奥に移す（既定の深さ上限 2 では追えない）。"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    _finish(user, order)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)


def _finish(user: User, order: Order) -> None:
    _release(user, order)


def _release(user: User, order: Order) -> None:
    _restore(user, order)


def _restore(user: User, order: Order) -> None:
    if user.is_admin:
        inventory.restore(order.items)
