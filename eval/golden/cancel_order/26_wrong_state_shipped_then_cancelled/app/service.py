"""注文キャンセルサービス（Golden 26_wrong_state_shipped_then_cancelled 用の変異版）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
成功経路で order.status を一度 SHIPPED に更新してから CANCELLED に更新し直す（同じ変数への
2 回の write）。「状態変更後に評価した条件は変更後の値に基づいて計算する」により
`after.order.status` は最後の更新値（CANCELLED）で評価されるため、R-CANCEL-03 は
VERIFIED_IN_SCOPE のままになる。R-CANCEL-07a（拒否経路での order.status への書込み 0）は
拒否経路（403/409）だけを対象にしており、この 2 回の write はどちらも成功経路にしかないため
影響を受けない。したがって全要求が VERIFIED_IN_SCOPE のままになる。
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
    order.status = OrderStatus.SHIPPED
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
