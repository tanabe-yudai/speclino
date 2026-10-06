"""費用ガード。usage_metadata から推定費用を計算し、日次の累積枠で実行を制御する。

予算アラートは支出を止めないので、実行前に枠を確認し、超過時はエージェントを起動しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

# 日次の費用枠の予約を直列化する advisory lock の鍵（固定）。議論役（S59）と会話（S73）が共有する
COST_LOCK_KEY = 0x53504C494E4F01

# 100 万トークンあたりの USD（2026-09 時点の公開価格。実装時に確認済みの値を settings で上書き可）
DEFAULT_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),
    "gemini-3.5-flash": (Decimal("1.50"), Decimal("9.00")),
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
    "scripted": (Decimal("0"), Decimal("0")),
}


class CostLedger(Protocol):
    async def add(
        self,
        day: date,
        run_id: str | None,
        estimated_usd: Decimal,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None: ...

    async def total_for_day(self, day: date) -> Decimal: ...


@dataclass
class InMemoryLedger:
    rows: list[tuple[date, Decimal]]

    async def add(
        self,
        day: date,
        run_id: str | None,
        estimated_usd: Decimal,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None:
        self.rows.append((day, estimated_usd))

    async def total_for_day(self, day: date) -> Decimal:
        return sum((usd for d, usd in self.rows if d == day), Decimal("0"))


@dataclass
class CostGuard:
    ledger: CostLedger
    daily_limit_usd: Decimal
    per_run_limit_usd: Decimal = Decimal("0.50")
    pricing: dict[str, tuple[Decimal, Decimal]] | None = None

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        pin, pout = (self.pricing or DEFAULT_PRICING).get(
            model, DEFAULT_PRICING["gemini-3.8-flash"]
        )
        return (pin * input_tokens + pout * output_tokens) / Decimal(1_000_000)

    async def can_start(self, today: date) -> tuple[bool, str]:
        used = await self.ledger.total_for_day(today)
        if used + self.per_run_limit_usd > self.daily_limit_usd:
            return False, f"日次の費用枠 ${self.daily_limit_usd} に到達（使用 ${used:.4f}）"
        return True, f"残り ${self.daily_limit_usd - used:.4f}"

    async def can_afford(self, today: date, worst_case_usd: Decimal) -> tuple[bool, str]:
        """1 回の呼出しの最悪費用が、今日の残り枠に収まるか（S46 仕様 PR bot）。

        `can_start` の固定の余裕（per_run_limit_usd）と違い、呼出しごとに見積もった上限で
        判断する。呼出し後に実費を `record` しても、超過してからでは止められないため。
        """
        used = await self.ledger.total_for_day(today)
        remaining = self.daily_limit_usd - used
        if worst_case_usd > remaining:
            return False, (
                f"最悪の費用 ${worst_case_usd:.4f} が日次の残り枠 ${remaining:.4f}"
                f"（枠 ${self.daily_limit_usd}）を超える"
            )
        return True, f"最悪 ${worst_case_usd:.4f} / 残り ${remaining:.4f}"

    async def record(
        self, today: date, run_id: str | None, model: str, input_tokens: int, output_tokens: int
    ) -> Decimal:
        usd = self.estimate(model, input_tokens, output_tokens)
        await self.ledger.add(today, run_id, usd, input_tokens, output_tokens, model)
        return usd
