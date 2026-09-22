"""インプロセスのタスクキュー（ローカル・テスト用）。

`immediate=True` なら enqueue の中で handler を await する（テストで結果をすぐ確認できる）。
`immediate=False` なら asyncio のタスクとして背景で実行する（`make dev` の単一プロセス構成）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

Handler = Callable[[UUID], Coroutine[Any, Any, None]]


class InProcessQueue:
    def __init__(self, handler: Handler, immediate: bool = True) -> None:
        self.handler = handler
        self.immediate = immediate
        self.enqueued: list[UUID] = []
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue(self, run_id: UUID) -> str:
        if run_id in self.enqueued:
            return f"inprocess:{run_id}:duplicate"
        self.enqueued.append(run_id)
        if self.immediate:
            await self.handler(run_id)
        else:
            task: asyncio.Task[None] = asyncio.create_task(self.handler(run_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return f"inprocess:{run_id}"

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
