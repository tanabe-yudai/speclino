"""注文キャンセルサービス（Golden 25_frozen_rerun 用: 同じ凍結入力で再実行する）。

`examples/orders/app/service.py`（正しい実装）と同一。import は `examples.orders.app` の
まま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。このケースは実装ではなく
再現性（同じ凍結入力で再実行すると、時刻等を除いた結果が一致すること）を
確認するためのもの。`tests/unit/eval/test_golden.py` でこのケースを含む golden 全体を
2 回評価し、run_id を除いた findings が一致することを確認する。
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
