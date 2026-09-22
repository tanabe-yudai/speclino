"""注文キャンセルサービス（Golden 18_loop_in_path 用の変異版: 関連経路に対応外ループがある）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
成功経路で `inventory.restore(order.items)` の代わりに `for item in order.items:
inventory.restore(item)` を使う。`ast.For` は PoC 3.2 の対応構文（対応構文一覧は
`src/speclino/frontend/python/subset.py`）に含まれないため、path_analyzer はこの文に到達した
時点でその経路の探索を打ち切り、outcome を unresolved・precision を incomplete として終える
（in-loop の呼出しや、ループより後の audit.record / return も一切観測されない）。成功経路
だけに関連する R-CANCEL-03/04/05/06 が UNKNOWN になり、拒否経路（403/409）に関連する
R-CANCEL-01/02/07a/07b/07c は影響を受けない。
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
    for item in order.items:
        inventory.restore(item)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
