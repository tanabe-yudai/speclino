"""注文サービス（spec.md に適合する実装）。

path_analyzer の対応構文（if/raise、属性の参照と代入、比較、直接の呼出し）だけで書く。
在庫・決済・通知は注入されたオブジェクト（`self.inventory` など）を呼ぶ。
`examples/shop/mutations/*.patch` はこのファイルをわざと壊す差分。
"""

from __future__ import annotations

from examples.shop.app.inventory import Inventory
from examples.shop.app.mailer import Mailer
from examples.shop.app.models import Order, OrderError, OrderStatus, Stock
from examples.shop.app.payment import Payment


class OrderService:
    def __init__(self, inventory: Inventory, payment: Payment, mailer: Mailer) -> None:
        self.inventory = inventory
        self.payment = payment
        self.mailer = mailer

    def place_order(self, order: Order, stock: Stock) -> None:
        if order.status != OrderStatus.PENDING:
            raise OrderError("確定できるのは未確定の注文だけです。注文の状態を確認してください。")
        if stock.available < order.quantity:
            raise OrderError(
                "在庫が足りないため注文を確定できません。数量を減らしてやり直してください。"
            )
        self.inventory.reserve(order.id, order.quantity)
        self.payment.charge(order.id)
        order.status = OrderStatus.PAID

    def ship_order(self, order: Order) -> None:
        if order.status != OrderStatus.PAID:
            raise OrderError(
                "出荷できるのは決済済みの注文だけです。決済を済ませてから出荷してください。"
            )
        order.status = OrderStatus.SHIPPED
        self.mailer.notify(order.id)

    def cancel_order(self, order: Order) -> None:
        if order.status != OrderStatus.PENDING:
            raise OrderError(
                "取り消せるのは未確定の注文だけです。決済済みの注文は返品の手続きをしてください。"
            )
        order.status = OrderStatus.CANCELLED
        self.mailer.notify(order.id)
