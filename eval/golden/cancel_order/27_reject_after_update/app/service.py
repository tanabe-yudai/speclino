"""注文キャンセルサービス（Golden 27_reject_after_update 用の変異版: 状態を更新してから拒否する）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
認可されていない利用者を 403 で拒否する直前に order.status を CANCELLED へ書き込む（本来
拒否する経路で状態を変更してしまう不具合）。この write を認可判定より前（無条件）に置くと
後続の `order.status != OrderStatus.PENDING` の判定が更新後の定数値で評価されてしまい、
成功経路自体が到達不能になって無関係な要求（R-CANCEL-02〜06）まで巻き込む。
それは意図した不具合の再現ではないため、write は「拒否される分岐の内部」（403 を raise する
直前）に限定する。これにより拒否経路（p1_forbidden）だけで order.status への書込みが発生し、
R-CANCEL-07a（拒否経路での書込み 0 を要求）が forbidden_effect で VIOLATED になる。
認可・状態判定・成功系の要求（R-CANCEL-01〜06、R-CANCEL-07b/07c）は影響を受けない。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        order.status = OrderStatus.CANCELLED
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
