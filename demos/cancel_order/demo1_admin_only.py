"""注文キャンセルサービス（正しい実装）。

specs/orders-cancel.md の R-CANCEL-01〜07 に対応する。解析器が対応する構文
（if/elif/else、早期 raise、単純な属性参照・代入、and/or/not、直接呼び出し）だけで書き、
ループ・try・デコレーター・内包表記・f文字列・getattr は使わない。

認可判定（R-CANCEL-01）を状態判定（R-CANCEL-02）より先に行う。
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
