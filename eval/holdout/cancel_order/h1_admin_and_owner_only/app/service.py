"""注文キャンセルサービス（Holdout h1_admin_and_owner_only 用の変異版）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ。認可条件を「管理者 OR 所有者」から「管理者 AND 所有者」に
変える。所有者だけ（管理者ではない）の利用者は認可対象から外れ 403 になるため、
R-CANCEL-02〜06（認可された利用者を前提とする要求）が所有者のみの入力を巻き込んで違反になる。
holdout（調整に使わない検証用）ケースであり、`make eval` には含めない。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin and user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
