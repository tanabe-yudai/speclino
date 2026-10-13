"""注文キャンセルサービス（Golden 21_mock_only 用の変異版: モックへの呼び出しだけがある）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
`inventory.restore` の代わりに、同一ファイル内に定義したテストダブル `_mock_restore` を呼ぶ。
`_mock_restore` は bindings.operations に登録されていないリポジトリ内関数として展開される
（既定の呼出し深さ上限 1 で足りる）が、本体が `pass` のため呼出しイベントを何も発行しない。
その結果 inventory_restore_command の呼出し回数は 0 のまま確定し（経路は exact_in_model の
ままなので UNKNOWN にはならない）、R-CANCEL-04 が missing_effect の VIOLATED になる。
モック相手の呼び出しは永続化の証拠にならないことを、実装が「一見正しく
見える」形で示す。
"""

from __future__ import annotations

from fastapi import HTTPException, Response

from examples.orders.app import audit, inventory
from examples.orders.app.models import AuditAction, Order, OrderStatus, User


def _mock_restore(items: tuple[str, ...]) -> None:
    """在庫復元コマンドのテストダブル。呼ばれても実際の効果もイベントも残らない。"""
    pass


def cancel_order(user: User, order: Order) -> Response:
    if not (user.is_admin or user.id == order.owner_id):
        raise HTTPException(status_code=403)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409)
    order.status = OrderStatus.CANCELLED
    _mock_restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
