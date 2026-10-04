"""議論役のスキルの単体テスト用の偽物（TaskUpdater・SkillCall・context）。Task 5〜7 で共用する。"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from a2a.helpers import get_data_parts, get_text_parts

from speclino.a2a_server.executor import SkillCall
from speclino.agent.consultant.tools import ConsultBudget, ConsultDeps, ConsultTools, guarded
from speclino.agent.gateway import InMemoryAudit
from speclino.domain.a2a import CostReservation, SkillRequest, TokenIdentity
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.mcp_context import read_context

ORDERS = Path(__file__).resolve().parents[4] / "examples" / "orders"
TEAM = "e2e-ws-a"


@dataclass
class FakeMessage:
    parts: list[Any]
    metadata: dict[str, Any] | None = None


@dataclass
class RecordingUpdater:
    """TaskUpdater の代わり。呼ばれたメソッドと引数を順に記録する。"""

    calls: list[tuple[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def new_agent_message(
        self, parts: list[Any], metadata: dict[str, Any] | None = None
    ) -> FakeMessage:
        return FakeMessage(list(parts), metadata)

    async def requires_input(self, message: FakeMessage | None = None) -> None:
        self.calls.append(("requires_input", message))

    async def start_work(self, message: FakeMessage | None = None) -> None:
        self.calls.append(("start_work", message))

    async def add_artifact(self, parts: list[Any], name: str | None = None, **kw: Any) -> None:
        self.artifacts.append({"name": name, "parts": list(parts), **kw})
        self.calls.append(("add_artifact", name))

    async def complete(self, message: FakeMessage | None = None) -> None:
        self.calls.append(("complete", message))

    async def failed(self, message: FakeMessage | None = None) -> None:
        self.calls.append(("failed", message))

    def _last_message(self) -> FakeMessage:
        for _, arg in reversed(self.calls):
            if isinstance(arg, FakeMessage):
                return arg
        raise AssertionError("メッセージが無い")

    def last_text(self) -> str:
        return "\n".join(get_text_parts(self._last_message().parts))

    def last_metadata(self) -> dict[str, Any]:
        return self._last_message().metadata or {}

    def _artifact(self, name: str) -> dict[str, Any]:
        for a in self.artifacts:
            if a["name"] == name:
                return a
        raise AssertionError(f"成果物 {name} が無い")

    def artifact_text(self, name: str) -> str:
        return "\n".join(get_text_parts(self._artifact(name)["parts"]))

    def artifact_json(self, name: str) -> Any:
        [data] = get_data_parts(self._artifact(name)["parts"])
        return data


def fake_call(
    *,
    skill: str = "spec_review",
    text: str = "",
    repo: str | None = "acme/orders",
    reply: str | None = None,
    state: dict[str, Any] | None = None,
    team: str = TEAM,
    **fields: Any,
) -> SkillCall:
    request = SkillRequest.model_validate({"skill": skill, "repo": repo, "text": text, **fields})
    return SkillCall(
        request,
        TokenIdentity(team=team, token_id=uuid.uuid4()),
        RecordingUpdater(),  # type: ignore[arg-type]
        "task-1",
        reply,
        copy.deepcopy(state) if state is not None else None,
        InMemoryAudit(),
    )


def demo_context() -> dict[str, Any]:
    """examples/orders の語彙・規約・既存の要求（MCP の context_get と同じ形）。"""
    return read_context(ORDERS)


class FakeContextSource:
    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context

    async def get(self, project_id: uuid.UUID, workspace: str, ref: str) -> dict[str, Any]:
        return copy.deepcopy(self.context)


async def stub_fetch_spec_context(
    self: ConsultTools, repo: str, ref: str, *, truncate: bool
) -> dict[str, Any]:
    """DB の project 照合を飛ばし、deps.context_source の context を門番を通して返す。"""

    async def run() -> dict[str, Any]:
        result: dict[str, Any] = await self._deps.context_source.get(
            uuid.uuid4(), self._identity.team, ref
        )
        return result

    return await guarded(
        self._gateway, "spec_context", {"repo": repo, "ref": ref}, run, truncate=truncate
    )


def fake_deps(
    *, llm: Any = None, context: dict[str, Any] | None = None, cost_guard: Any = None
) -> ConsultDeps:
    return ConsultDeps(
        session_factory=MagicMock(),
        context_source=FakeContextSource(context if context is not None else demo_context()),
        service=MagicMock(),
        cost_guard=cost_guard,
        spec_llm=llm,
        agent_model="scripted",
        today=lambda: date(2026, 10, 4),
        load_run_view=AsyncMock(return_value=None),
        record_supplement=AsyncMock(),
    )


# scripted の単価は 0 なので、予約の戻し忘れが台帳の差に出るよう単価を付ける
PRICED_SCRIPTED = {"scripted": (Decimal("1"), Decimal("1"))}


def priced_guard(limit: str = "10") -> CostGuard:
    return CostGuard(InMemoryLedger([]), Decimal(limit), pricing=PRICED_SCRIPTED)


class LedgerBudget(ConsultBudget):
    """予約と精算を記録する偽の台帳。allow 回だけ予約を通す。net は台帳の正味の増減。"""

    def __init__(self, allow: int = 99) -> None:
        self.allow = allow
        self._guard = priced_guard()
        self.reserved: list[Decimal] = []
        self.settled: list[tuple[Decimal, int, int]] = []
        self.net = Decimal(0)

    async def reserve(
        self, today: date, model: str, worst_usd: Decimal
    ) -> tuple[CostReservation | None, str]:
        if len(self.reserved) >= self.allow:
            return None, "日次の残り枠を超える"
        self.reserved.append(worst_usd)
        self.net += worst_usd
        return CostReservation(day=today, model=model, worst_usd=worst_usd), "ok"

    async def settle(
        self, reservation: CostReservation, input_tokens: int, output_tokens: int
    ) -> None:
        self.settled.append((reservation.worst_usd, input_tokens, output_tokens))
        actual = self._guard.estimate(reservation.model, input_tokens, output_tokens)
        self.net += actual - reservation.worst_usd


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
