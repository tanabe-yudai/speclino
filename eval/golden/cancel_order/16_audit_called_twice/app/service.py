"""注文キャンセルサービス（Golden 16_audit_called_twice 用の変異版）。

PoC 17.1 は「未記載の通知を追加する → REVIEW。禁止と明記されていれば VIOLATED」を求める。
しかし conformance_engine の undocumented_behavior 検出（PoC 10.5）は
`code.paths[*].events[*]` のうち kind="call" のイベント（= bindings.operations に登録され
`_emit_operation` で発行されたイベント）だけを対象にする。このケースで触ってよいのは
`eval/golden/cancel_order/` 配下の新規ケースディレクトリだけで、bindings.yaml
（在庫復元・監査記録の 2 操作のみを登録）と、source モードの一時リポジトリへコピーする
ファイル一覧を決めている `runner._source_repo`（`src/speclino/eval/runner.py` の指標レポート
拡張の対象外）のどちらも変更できない。したがって「bindings に無い新しい呼び出し」を
source モードの解析結果として発生させる経路が無く（未登録シンボルへの呼出しは call イベント
ではなく unresolved_call の unknown_causes になるため、undocumented_behavior の判定対象に
そもそも乗らない）、この行を額面通りに再現することはできない。代わりに、同じ「仕様が把握して
いない追加の振る舞い」という主旨を、検出可能な形（登録済み操作の呼出し回数が想定と異なる）で
再現する: audit_record_command を成功経路で 2 回呼ぶ。R-CANCEL-05（count eq 1）のみ
cardinality_mismatch になる。
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
    audit.record(user.id, AuditAction.CANCEL_ORDER)
    return Response(status_code=204)
