"""在庫（記録用スタブ）。bindings の `Inventory.reserve` が指す。"""

from __future__ import annotations


class Inventory:
    def __init__(self) -> None:
        self.reserved: list[tuple[str, int]] = []

    def reserve(self, order_id: str, quantity: int) -> None:
        self.reserved.append((order_id, quantity))
