"""注文キャンセルサービス（Golden 20_admin_only_test_observed 用の変異版）。

`07_restore_admin_only` と同一コード（在庫復元を `if user.is_admin:` で管理者経路だけに
限定する不具合）。静的解析の結果は 07 と同じになる。このケースは「管理者のテストだけを
観測した → 所有者経路を観測済みにしない」という動的観測（test_run）側の論点を、Golden の
静的評価の枠組みで文書化するために追加した。今回の S34 スコープでは trace_importer /
test_run（`src/speclino/agent/tools/test_run.py`）と runner を接続しないため、
`Finding.test_observation` は生成しない。case.yaml の description で「管理者のテストが
通っても所有者経路を観測済みにはしない」という設計意図を明記する。手書き IR
（expected-code-ir.json）は 07 のものを流用する。
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
        inventory.restore(order.items)
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
