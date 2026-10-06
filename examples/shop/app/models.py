"""ショップ題材のドメイン型（spec.md のクラス図と `.speclino/domain.yaml` に対応）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderStatus(Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


class OrderError(Exception):
    """注文の操作を受け付けられない（状態が違う・在庫が足りない）。"""


@dataclass
class Order:
    id: str
    quantity: int
    status: OrderStatus


@dataclass
class Stock:
    """注文を受けた時点の在庫（クラス図の Inventory.available）。"""

    available: int
