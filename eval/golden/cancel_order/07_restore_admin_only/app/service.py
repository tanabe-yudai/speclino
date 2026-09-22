"""注文キャンセルサービス（Golden 07_restore_admin_only 用の変異版: 在庫復元を管理者経路だけで呼ぶ）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
"""

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
    if user.is_admin:
        inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
