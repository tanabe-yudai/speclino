"""注文キャンセルサービス（Golden 14_equivalent_condition 用の変異版: 同等の条件式へ書き換える）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
`not (a or b)` をド・モルガンの法則で `(not a) and (not b)` に、`!= PENDING` を
`not (== PENDING)` に書き換える。論理的に同値なので Z3 上は同じ充足可能性になり、
PoC 17.1「同等の条件式へ書き換える → 誤った違反を出さない」を確認できる。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if (not user.is_admin) and (not (user.id == order.owner_id)):
        raise HTTPException(status_code=403)
    if not (order.status == OrderStatus.PENDING):
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
