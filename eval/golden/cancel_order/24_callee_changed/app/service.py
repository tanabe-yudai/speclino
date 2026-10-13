"""注文キャンセルサービス（Golden 24_callee_changed 用の変異版: 呼び出し先だけを変更する）。

15_helper_split（在庫復元と監査記録をヘルパー `_finish(order, user)` に分割した実装）を基に、
`cancel_order` 自体は変更せず `_finish` の中身だけを変更する（在庫復元の呼出しを削除する）。
呼出し元 `cancel_order` の差分はゼロでも、呼出し先 `_finish` を経由して展開される Code IR が
変わるため、`_finish` に依存する入口の要求（R-CANCEL-04）を再評価すると missing_effect になる
ことを確認する（「変更行に要求 ID を紐付けるだけでは不十分。変更されたヘルパーに依存
する入口の要求も再評価する」）。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def _finish(order: Order, user: User) -> None:
    audit.record(user.id, AuditAction.CANCEL_ORDER)


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    _finish(order, user)
    return Response(status_code=204)
