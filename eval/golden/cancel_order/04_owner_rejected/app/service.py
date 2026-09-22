"""注文キャンセルサービス（Golden 04_owner_rejected 用の変異版: 所有者を拒否し管理者だけ許す）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not user.is_admin:
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
