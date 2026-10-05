"""テストで共有する仕様の見本（grammar.md の例）。"""

from __future__ import annotations


def block(*lines: str) -> str:
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


CLASSES = block(
    "%%@ id: vocab",
    "classDiagram",
    "    direction LR",
    "    %% 注文まわりの語彙",
    "    class OrderService {",
    "        +checkout(order, payment)",
    "        -cancel(order_id)",
    "    }",
    "    class InventoryService {",
    "        +reserve(order)",
    "    }",
    "    class PaymentGateway {",
    "        +charge(order)",
    "    }",
    "    class Order {",
    "        int qty",
    "        bool paid",
    "        opaque_id order_id",
    "        OrderStatus status",
    "    }",
    "    class OrderStatus {",
    "        <<enumeration>>",
    "        PENDING",
    "        PAID",
    "        CANCELLED",
    "    }",
)

SEQUENCE = block(
    "%%@ id: checkout",
    "%%@ entry: OrderService.checkout",
    "sequenceDiagram",
    "    participant S as OrderService",
    "    participant I as InventoryService",
    "    participant P as PaymentGateway",
    "    S->>I: [m1] reserve(order)",
    "    I-->>S: [r1] 確保した",
    "    alt [g1] before.order.qty > 0",
    "        S->>P: [m2] charge(order)",
    "    else [g2]",
    "        S->>I: [m3] release",
    "    end",
)

STATES = block(
    "%%@ id: order-status",
    "%%@ attribute: order.status",
    "stateDiagram-v2",
    "    [*] --> PENDING",
    "    PENDING --> PAID: [t1] pay [before.order.status == OrderStatus.PENDING]",
    "    PENDING --> CANCELLED: [t2] cancel",
    "    PAID --> [*]",
)

ACTIVITY = block(
    "%%@ id: cancel-flow",
    "%%@ entry: OrderService.cancel",
    "%%@ closed: true",
    "flowchart TD",
    "    a((start))",
    "    d{支払い済み?}",
    "    r[PaymentGateway.refund]",
    "    z((end))",
    "    a -->|[e1]| d",
    "    d -->|[e2] before.order.paid| r",
    "    d -->|[e3] not before.order.paid| z",
    "    r -->|[e4]| z",
)

REQUIREMENTS = (
    "| ID | level | 要求 | 形式化 |\n"
    "| --- | --- | --- | --- |\n"
    "| R1 | mandatory | 在庫を確保してから課金する | checkout#m1, checkout#m2 |\n"
    "| R2 | advisory | 取消は返金する | cancel-flow#r |\n"
    "| R3 | mandatory | 監査ログを残す | - |\n"
)

FULL = (
    "# 注文\n\n" + REQUIREMENTS + "\n" + CLASSES + "\n" + SEQUENCE + "\n" + STATES + "\n" + ACTIVITY
)
