"""注文キャンセルサービス（Golden 22_unsat_applicability 用）。

コードは `examples/orders/app/service.py`（正しい実装）と同一。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
このケースは実装ではなく仕様側の欠陥（適用条件が充足不能）を確認するため、`case.yaml` の
`spec_ir_override` でケース固有の `spec-ir.json` を使う。R-CANCEL-04 だけ applicability を
`before.order.status == PENDING and before.order.status == SHIPPED`（同時に成り立たない）
に差し替えてある。
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
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
