"""注文キャンセルサービス（Holdout h3_swapped_status_codes 用の変異版）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ。認可エラーと状態競合エラーの応答コードを入れ替える
（403 ↔ 409）。R-CANCEL-01（無権限者は403のはず）と R-CANCEL-02（非PENDINGは409のはず）が
どちらも outcome_mismatch になる（403/409 とも非成功域のため成功/拒否の区分自体は変わらない）。
holdout（調整に使わない検証用）ケースであり、`make eval` には含めない。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=409)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=403)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
