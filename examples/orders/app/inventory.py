"""在庫復元コマンド（記録用スタブ）。

`.speclino/bindings.yaml` の `operations.inventory_restore_command` が参照するシンボル。
実際の在庫更新は行わず、呼び出しを `restored_calls` に記録するだけの観測用スタブとする
（コマンド呼び出しの観測と永続化の成否は別）。
"""

from __future__ import annotations

restored_calls: list[tuple[str, ...]] = []


def restore(items: tuple[str, ...]) -> None:
    restored_calls.append(items)
