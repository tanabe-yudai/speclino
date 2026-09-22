"""注文キャンセルサービス（Golden 19_path_limit 用の変異版: 解析上限・ソルバー制限に達する）。

`examples/orders/app/service.py`（正しい実装）を変異させたもの。import は
`examples.orders.app` のまま保つ（S17 の path_analyzer 入力に使う。S09 では未実行）。
成功経路に、意味の無い独立した if/else を 8 個並べる（`AnalysisLimits.max_paths` の既定値は
64、`src/speclino/analysis/limits.py`）。path_analyzer は分岐を意味的に刈り込まず愚直に
2 分岐ずつ展開するため、8 個の独立した if で 2^8=256 通りの成功経路が生じ、既存の 403/409
経路と合わせて上限 64 を超える。上限超過時 `PathAnalyzer.analyze` は `PathLimitExceeded` を
送出し、収集済みの経路を全て捨てて `code.paths=[]`・`code.unknown_causes=[limit_paths]` を
返す（`src/speclino/analysis/path_analyzer.py` の `analyze` 参照）。conformance_engine は
経路が 1 つも無いと網羅性チェック（ステップ4）で `unsupported_analysis` の UNKNOWN に倒すため、
R-CANCEL-01〜07c の全要求が UNKNOWN になる。
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
    if user.is_admin:
        pass
    else:
        pass
    if user.id == order.owner_id:
        pass
    else:
        pass
    if order.status == OrderStatus.PENDING:
        pass
    else:
        pass
    if order.status == OrderStatus.SHIPPED:
        pass
    else:
        pass
    if order.status == OrderStatus.CANCELLED:
        pass
    else:
        pass
    if user.is_admin:
        pass
    else:
        pass
    if user.id == order.owner_id:
        pass
    else:
        pass
    if order.status == OrderStatus.PENDING:
        pass
    else:
        pass
    inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
