"""AI の書き換え提案（S67）の試験で共有する本文と台本の応答。"""

from __future__ import annotations

import json

from tests.unit.spec.uml.samples import FULL

GOOD_GUARD = "[before.order.status == OrderStatus.PENDING]"
# 自然言語のガード（E_GUARD_SYNTAX）と、それに伴う E_UNKNOWN_STATE の 2 件のエラーがある本文
BAD = FULL.replace(GOOD_GUARD, "[注文が未払いのとき]")
assert BAD != FULL
BAD_LINE_NO = next(i for i, ln in enumerate(BAD.split("\n"), 1) if "注文が未払いのとき" in ln)
BAD_LINE = BAD.split("\n")[BAD_LINE_NO - 1]
FIXED_LINE = BAD_LINE.replace("[注文が未払いのとき]", GOOD_GUARD)
# BAD のガードを式に直す 1 件の提案（行番号で範囲を答える）
RESPONSE = json.dumps(
    {
        "suggestions": [
            {
                "start_line": BAD_LINE_NO,
                "end_line": BAD_LINE_NO,
                "replacement": FIXED_LINE,
                "reason": "自然言語のガードを式にする",
            }
        ]
    },
    ensure_ascii=False,
)

__all__ = ["BAD", "BAD_LINE", "BAD_LINE_NO", "FIXED_LINE", "FULL", "GOOD_GUARD", "RESPONSE"]
