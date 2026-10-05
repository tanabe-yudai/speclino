"""利用者への通知（記録用スタブ）。bindings の `Mailer.notify` が指す。"""

from __future__ import annotations


class Mailer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def notify(self, order_id: str) -> None:
        self.sent.append(order_id)
