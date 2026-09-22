"""注文キャンセルサービス（Holdout h2_audit_removed 用の変異版）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ。成功経路から audit.record（監査記録コマンド）の呼出しを
削除する。R-CANCEL-05（監査記録を 1 回呼ぶ）のみ missing_effect になる。holdout（調整に
使わない検証用）ケースであり、`make eval` には含めない。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import inventory
from examples.orders.app.models import Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    return Response(status_code=204)
