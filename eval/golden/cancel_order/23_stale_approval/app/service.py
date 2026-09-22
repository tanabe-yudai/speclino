"""注文キャンセルサービス（Golden 23_stale_approval 用）。

コードは `examples/orders/app/service.py`（正しい実装）と同一。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
このケースは実装ではなく仕様側の状態（承認切れ）を確認するため、`case.yaml` の
`spec_ir_override` でケース固有の `spec-ir.json` を使う。R-CANCEL-04 だけ
`approval_ref` を `null` にしてあり、承認記録（approvals）に対応する承認が無い状態を表す
（仕様本文が承認後に変わり、まだ再承認されていない、という想定）。
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
