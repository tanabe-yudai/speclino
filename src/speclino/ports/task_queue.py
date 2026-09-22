"""TaskQueue ポート: run の実行を worker に依頼する（Cloud Tasks / インプロセス）。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class TaskQueue(Protocol):
    async def enqueue(self, run_id: UUID) -> str:
        """run の実行を依頼し、タスク識別子を返す。同じ run_id の重複投入は排除する。"""
        ...


__all__ = ["TaskQueue"]
