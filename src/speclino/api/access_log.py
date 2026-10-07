"""アクセスログから秘密を伏せる（S75）。

`GET /api/sync/plans/{token}` はパスに使い捨ての取得トークン（`slplan_…`）を含む。uvicorn の
アクセスログ（`uvicorn.access`）にそのまま残さないよう、`slplan_***` に置き換えるフィルタを付ける。
"""

from __future__ import annotations

import logging
import re

_PLAN_TOKEN = re.compile(r"slplan_[A-Za-z0-9_\-]+")
_MASK = "slplan_***"


def redact(text: str) -> str:
    return _PLAN_TOKEN.sub(_MASK, text)


class RedactPlanTokens(logging.Filter):
    """ログの本文と引数の `slplan_…` を伏せる（記録は捨てない）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install(logger_name: str = "uvicorn.access") -> None:
    """フィルタを 1 度だけ付ける（create_app が呼ばれるたびに増やさない）。"""
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, RedactPlanTokens) for f in logger.filters):
        logger.addFilter(RedactPlanTokens())


__all__ = ["RedactPlanTokens", "install", "redact"]
