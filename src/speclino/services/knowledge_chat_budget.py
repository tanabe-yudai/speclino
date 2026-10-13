"""会話の費用の予約と精算（S73）。

既存の日次の枠（`ConsultBudget` と同じ台帳・同じ advisory lock）に加えて、1 人で共有の枠を
使い切らないように次を原子的に数える:

- ユーザーごと: 1 日の発言（`purpose='chat_message'` の予約）が `user_messages_per_day` 未満、
  1 日の費用が全体の枠の `user_share` 以下
- ワークスペースごと: 1 日の費用が全体の枠の `workspace_share` 以下

予約の行と精算の行はどちらも workspace / uid を持つので、精算の後はその人・そのワークスペースの
実費で数え直せる。拒否の理由は固定のコード（`cost_limit` / `message_limit`）だけを返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.services.cost_guard import COST_LOCK_KEY, CostGuard

ChatPurpose = Literal["chat_message", "chat_summary"]
BudgetRefusal = Literal["cost_limit", "message_limit"]


@dataclass(frozen=True)
class ChatLimits:
    user_messages_per_day: int = 50
    user_share: Decimal = Decimal("0.50")
    workspace_share: Decimal = Decimal("0.50")


@dataclass(frozen=True)
class ChatReservation:
    day: date
    model: str
    worst_usd: Decimal
    workspace: str
    uid: str


class ChatBudget:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cost_guard: CostGuard,
        limits: ChatLimits | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._guard = cost_guard
        self._limits = limits or ChatLimits()

    async def reserve(
        self,
        today: date,
        model: str,
        worst_usd: Decimal,
        *,
        workspace: str,
        uid: str,
        purpose: ChatPurpose,
    ) -> tuple[ChatReservation | None, BudgetRefusal | None]:
        """予約できれば (予約, None)、できなければ (None, 理由のコード)。"""
        limit = self._guard.daily_limit_usd
        async with self._session_factory() as session, session.begin():
            await session.execute(
                sql_text("SELECT pg_advisory_xact_lock(:k)"), {"k": COST_LOCK_KEY}
            )
            ledger = CostLedgerRepository(session)
            if purpose == "chat_message":
                sent = await ledger.count_reservations(today, uid=uid, purpose=purpose)
                if sent >= self._limits.user_messages_per_day:
                    return None, "message_limit"
            used = await ledger.total_for_day(today)
            # 検査のパイプライン 1 回分の余裕を残す（ConsultBudget と同じ）
            if used + worst_usd + self._guard.per_run_limit_usd > limit:
                return None, "cost_limit"
            mine = await ledger.total_for_day(today, uid=uid)
            if mine + worst_usd > limit * self._limits.user_share:
                return None, "cost_limit"
            ours = await ledger.total_for_day(today, workspace=workspace)
            if ours + worst_usd > limit * self._limits.workspace_share:
                return None, "cost_limit"
            await ledger.add(
                today, None, worst_usd, 0, 0, model, workspace=workspace, uid=uid, purpose=purpose
            )
        return ChatReservation(today, model, worst_usd, workspace, uid), None

    async def settle(
        self,
        reservation: ChatReservation,
        input_tokens: int,
        output_tokens: int,
        extra_usd: Decimal = Decimal("0"),
    ) -> None:
        """予約を実費に直す。失敗・取り消しのときは (0, 0) で予約を丸ごと戻す。

        `extra_usd` はトークンで数えない費用（Google 検索の問い合わせ、使用量の分からない途中の
        呼出しの最悪の額）。
        """
        actual = self._guard.estimate(reservation.model, input_tokens, output_tokens) + extra_usd
        async with self._session_factory() as session, session.begin():
            await CostLedgerRepository(session).add(
                reservation.day,
                None,
                actual - reservation.worst_usd,
                input_tokens,
                output_tokens,
                reservation.model,
                workspace=reservation.workspace,
                uid=reservation.uid,
            )


__all__ = ["BudgetRefusal", "ChatBudget", "ChatLimits", "ChatPurpose", "ChatReservation"]
