"""MCP `context_get` の取得元ポート（S54）。プロセス内と、worker への委譲の 2 実装がある。"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class McpContextSource(Protocol):
    async def get(self, project_id: uuid.UUID, workspace: str, ref: str) -> dict[str, Any]:
        """workspace に登録された project の ref 時点の語彙・規約・要求。

        project の登録先が workspace と違う（確認後に移された等）・返せないときは
        ContextUnavailable。
        """
        ...


__all__ = ["McpContextSource"]
