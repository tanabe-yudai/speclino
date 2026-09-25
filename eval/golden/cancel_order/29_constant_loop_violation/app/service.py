"""注文キャンセルサービス（Golden 29_constant_loop_violation 用の変異版: range(定数) ループで
在庫復元を 2 回呼ぶ）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。

`for _ in range(2): inventory.restore(order.items)` で在庫復元コマンドを 2 回呼ぶ（S41b:
range(定数) の展開）。path_analyzer はこれを
`_ = 0; inventory.restore(order.items); _ = 1; inventory.restore(order.items)` へ純粋な AST
書き換えとして展開してから解析するため、08_restore_twice（直書きで 2 回呼ぶケース）と同じ
Code IR になる。R-CANCEL-04 の「1 回」に反するので cardinality_mismatch で VIOLATED になる。
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
    for _ in range(2):
        inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
