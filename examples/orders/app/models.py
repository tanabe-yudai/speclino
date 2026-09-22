"""注文キャンセル題材のドメイン型。

`examples/orders/app/service.py` の正しい実装と、`.speclino/domain.yaml`（DomainSpec）の
enums / opaque_types に対応する最小限の型だけを持つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderStatus(Enum):
    PENDING = "PENDING"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


class AuditAction(Enum):
    CANCEL_ORDER = "CANCEL_ORDER"


@dataclass
class User:
    id: str
    is_admin: bool


@dataclass
class Order:
    id: str
    owner_id: str
    status: OrderStatus
    items: tuple[str, ...]
