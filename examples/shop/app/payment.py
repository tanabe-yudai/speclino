"""決済（記録用スタブ）。bindings の `Payment.charge` / `Payment.refund` が指す。"""

from __future__ import annotations


class Payment:
    def __init__(self) -> None:
        self.charged: list[str] = []
        self.refunded: list[str] = []

    def charge(self, order_id: str) -> None:
        self.charged.append(order_id)

    def refund(self, order_id: str) -> None:
        self.refunded.append(order_id)
