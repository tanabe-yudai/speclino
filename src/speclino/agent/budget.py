"""実行上限（仕様書 9 章: ツール呼出し 20 回、経過時間 10 分）。到達理由を記録する。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class BudgetState:
    max_tool_calls: int
    max_seconds: int
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    stop_reason: str | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def exhausted_reason(self) -> str | None:
        if self.stop_reason:
            return self.stop_reason
        if self.tool_calls >= self.max_tool_calls:
            return f"ツール呼出し上限 {self.max_tool_calls} 回に到達"
        if self.elapsed >= self.max_seconds:
            return f"経過時間上限 {self.max_seconds} 秒に到達"
        return None

    def is_exhausted(self) -> bool:
        """純粋な真偽判定（自己検証で pure_call 変数 budget_exhausted に対応）。"""
        return self.exhausted_reason() is not None

    def charge(self) -> None:
        self.tool_calls += 1

    def stop(self, reason: str) -> None:
        self.stop_reason = reason

    def snapshot(self) -> dict[str, object]:
        return {
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "elapsed_seconds": round(self.elapsed, 1),
            "max_seconds": self.max_seconds,
            "stop_reason": self.exhausted_reason(),
        }
