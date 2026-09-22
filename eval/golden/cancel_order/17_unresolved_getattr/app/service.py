"""注文キャンセルサービス（Golden 17_unresolved_getattr 用の変異版: 未解決の getattr で呼び出す）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
在庫復元を `getattr(inventory, "restore")(order.items)` で間接的に呼ぶ。呼出し先の式が
`ast.Call`（`getattr(...)` 自体の戻り値）であり `ast.Name` / `ast.Attribute` の連鎖に
還元できないため、path_analyzer は呼出し先を解決できず unresolved_call として記録するだけで
経路の探索は継続する（PoC 3.2「対応外」は黙って落とさず、影響する要求を UNKNOWN にする）。
成功経路が incomplete になり、その経路だけに関連する R-CANCEL-03/04/05/06 が UNKNOWN になる。
拒否経路（403/409, R-CANCEL-01/02/07a/07b/07c）はこの式に到達しないため影響を受けない。
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
    getattr(inventory, "restore")(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
