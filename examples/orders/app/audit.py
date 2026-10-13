"""監査記録コマンド（記録用スタブ）。

`.speclino/bindings.yaml` の `operations.audit_record_command` が参照するシンボル。
実際の監査ログ永続化は行わず、呼び出しを `recorded_calls` に記録するだけの観測用スタブとする
（コマンド呼び出しの観測と永続化の成否は別）。
"""

from __future__ import annotations

from examples.orders.app.models import AuditAction

recorded_calls: list[tuple[str, AuditAction]] = []


def record(actor_id: str, action: AuditAction) -> None:
    recorded_calls.append((actor_id, action))
