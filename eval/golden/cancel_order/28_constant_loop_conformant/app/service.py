"""注文キャンセルサービス（Golden 28_constant_loop_conformant 用の変異版: 定数回ループで契約済み
操作を呼ぶ）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。

在庫復元を `for _ in range(1): ...`、監査記録を要素数 1 の定数タプルの `for action in (...): ...`
で呼ぶ（S41b: 定数回反復の展開。range(定数) とタプルリテラルの両方を使う）。path_analyzer は
これを `_ = 0; inventory.restore(order.items)` /
`action = AuditAction.CANCEL_ORDER; audit.record(user.id, action)` へ純粋な AST 書き換えとして
展開してから解析するため、01_correct と同じ 1 回ずつの呼出しになり、全要求が VERIFIED_IN_SCOPE
のまま変わらない。
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
    for _ in range(1):
        inventory.restore(order.items)
    for action in (AuditAction.CANCEL_ORDER,):
        audit.record(user.id, action)
    return Response(status_code=204)
