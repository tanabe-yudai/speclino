"""注文キャンセルサービス（Golden 15_helper_split 用の変異版: 対応する直接ヘルパーへ処理を分割する）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
在庫復元と監査記録を同一ファイル内のヘルパー関数 `_finish` に分割する。bindings.yaml は
変更していないため `_finish` は bindings.operations に無いリポジトリ内関数として扱われ、
既定の呼出し深さ上限（2）以内で展開されるため、PoC 17.1「対応する直接ヘルパーへ処理を分割する
→ 正常に追跡する」を確認できる。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def _finish(order: Order, user: User) -> None:
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    _finish(order, user)
    return Response(status_code=204)
