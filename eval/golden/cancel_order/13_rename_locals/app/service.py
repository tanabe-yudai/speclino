"""注文キャンセルサービス（Golden 13_rename_locals 用の変異版: ローカル変数名だけを変える）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
entrypoint の引数名（user / order）は bindings.yaml の variables が実装上の参照
（"user.is_admin" 等）として直接埋め込んでいるため変更できない。そこで、02_wrong_state 等の
既存ケースには無い「認可判定・状態判定の結果を一度ローカル変数に受ける」形へ書き換え、その
ローカル変数名（_perm / _pending）を変える。式の構造は同一のため、「ローカル変数名だけを変える → 誤った意味差分を出さない」を確認できる。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    _perm = user.is_admin or user.id == order.owner_id
    if not _perm:
        raise HTTPException(status_code=403)
    _pending = order.status == OrderStatus.PENDING
    if not _pending:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
