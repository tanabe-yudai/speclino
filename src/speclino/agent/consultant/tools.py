"""議論役のツールと監査（S59）。

LLM に渡すツール（`spec_context` / `run_findings`）も、手順の側が呼ぶ内部の操作も、すべて
`guarded` で ToolGateway の権限・予算の判定と監査を通す。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from google.adk.models.base_llm import BaseLlm
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.agent.gateway import (
    MAX_RESULT_CHARS,
    InMemoryAudit,
    ToolCall,
    ToolGateway,
    ToolResult,
)
from speclino.agent.permissions import AgentPermissions
from speclino.db.repositories import ProjectRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.domain.a2a import TokenIdentity
from speclino.ports.mcp_context import McpContextSource
from speclino.ports.text_llm import TextLlm
from speclino.services.cost_guard import CostGuard
from speclino.services.mcp_context import ContextUnavailable
from speclino.services.review_run_service import ReviewRunService

# ADK のエージェントに渡すのは spec_context と run_findings だけ。残りは手順だけが呼ぶ
CONSULT_TOOLS: frozenset[str] = frozenset(
    {"spec_context", "run_findings", "spec_extract", "supplement_record", "verification_start"}
)
# 日次の費用枠の予約を直列化する advisory lock の鍵（固定）
_COST_LOCK_KEY = 0x53504C494E4F01


def consult_permissions(
    max_tool_calls: int = 8, max_seconds: int = 120, max_llm_calls: int = 6
) -> AgentPermissions:
    return AgentPermissions(
        allowed_tools=CONSULT_TOOLS,
        max_tool_calls=max_tool_calls,
        max_seconds=max_seconds,
        max_llm_calls=max_llm_calls,
        test_run_enabled=False,
    )


@dataclass
class ConsultDeps:
    session_factory: async_sessionmaker[AsyncSession]
    context_source: McpContextSource
    service: ReviewRunService
    cost_guard: CostGuard | None
    spec_llm: TextLlm | None
    agent_model: str | BaseLlm
    today: Callable[[], date]
    load_run_view: Callable[[uuid.UUID, str], Awaitable[dict[str, Any] | None]]
    record_supplement: Callable[..., Awaitable[None]]
    budget: ConsultBudget | None = None
    poll_seconds: float = 3.0
    wait_seconds: float = 180.0


class ConsultBudget:
    """Gemini 呼出しの日次の費用枠の予約。並行のリクエストが同じ残額を見て枠を超えないようにする。

    `reserve` は 1 つのトランザクションの中で advisory lock を取り、合計 ＋ 最悪の費用が枠に
    収まるときだけ最悪の費用を台帳に先に書く。`settle` は実費との差（負にもなる）を足して精算する。
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], cost_guard: CostGuard
    ) -> None:
        self._session_factory = session_factory
        self._guard = cost_guard

    async def reserve(self, today: date, model: str, worst_usd: Decimal) -> tuple[bool, str]:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                sql_text("SELECT pg_advisory_xact_lock(:k)"), {"k": _COST_LOCK_KEY}
            )
            ledger = CostLedgerRepository(session)
            used = await ledger.total_for_day(today)
            limit = self._guard.daily_limit_usd
            if used + worst_usd > limit:
                return False, (
                    f"最悪の費用 ${worst_usd:.4f} が日次の残り枠 ${limit - used:.4f}"
                    f"（枠 ${limit}）を超える"
                )
            await ledger.add(today, None, worst_usd, 0, 0, model)
            return True, f"最悪 ${worst_usd:.4f} / 残り ${limit - used - worst_usd:.4f}"

    async def settle(
        self, today: date, model: str, worst_usd: Decimal, input_tokens: int, output_tokens: int
    ) -> None:
        """予約を実費に直す。LLM が例外で終わったときは (0, 0) で予約を丸ごと戻す。"""
        actual = self._guard.estimate(model, input_tokens, output_tokens)
        async with self._session_factory() as session, session.begin():
            await CostLedgerRepository(session).add(
                today, None, actual - worst_usd, input_tokens, output_tokens, model
            )


async def guarded(
    gateway: ToolGateway,
    name: str,
    args: dict[str, Any],
    fn: Callable[[], Awaitable[dict[str, Any]]],
    *,
    truncate: bool = True,
) -> dict[str, Any]:
    """門番（権限・予算）を通して非同期の処理を 1 回だけ実行し、監査を残す。

    拒否は gateway.invoke の経路（実行せず監査して denied）。truncate は LLM に渡す値だけ
    長さを切る（手順の側は False で完全な値を受け取る）。
    """
    call = ToolCall(name, args)
    if gateway.deny_reason(call) is not None:
        return gateway.invoke(call).to_dict()
    gateway.budget.charge()
    started = time.monotonic()
    try:
        result = await fn()
    except Exception as e:
        gateway.audit.record_allowed(
            call,
            ToolResult(ok=False, payload={"error": type(e).__name__}),
            int((time.monotonic() - started) * 1000),
        )
        raise
    if truncate:
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            result = {"truncated": True, "preview": text[:MAX_RESULT_CHARS]}
    gateway.audit.record_allowed(
        call, ToolResult(ok=True, payload=result), int((time.monotonic() - started) * 1000)
    )
    return result


class ConsultTools:
    """ADK のエージェントに渡すツール。呼ぶたびに門番を通り、トークンのワークスペースに限る。"""

    def __init__(self, deps: ConsultDeps, identity: TokenIdentity, gateway: ToolGateway) -> None:
        self._deps = deps
        self._identity = identity
        self._gateway = gateway

    async def spec_context(self, repo: str, ref: str = "HEAD") -> dict[str, Any]:
        """リポジトリの仕様の語彙とチーム規約（context）を返す。"""

        async def run() -> dict[str, Any]:
            async with self._deps.session_factory() as session:
                project = await ProjectRepository(session).get_by_repo(repo)
            if project is None or project.team != self._identity.team:
                return {"error": "repo_not_registered"}
            try:
                return await self._deps.context_source.get(project.id, self._identity.team, ref)
            except ContextUnavailable as e:
                return {"error": str(e)}

        return await guarded(self._gateway, "spec_context", {"repo": repo, "ref": ref}, run)

    async def run_findings(self, run_id: str) -> dict[str, Any]:
        """検査（run）の状態・ゲート結果・Finding（反例を含む）を返す。"""

        async def run() -> dict[str, Any]:
            not_found = {"error": "run_not_found", "run_id": run_id}
            try:
                rid = uuid.UUID(run_id)
            except ValueError:
                return not_found
            view = await self._deps.load_run_view(rid, self._identity.team)
            return not_found if view is None else view

        return await guarded(self._gateway, "run_findings", {"run_id": run_id}, run)

    def functions(self) -> list[Callable[..., Awaitable[dict[str, Any]]]]:
        return [self.spec_context, self.run_findings]


async def persist_audit(
    session_factory: async_sessionmaker[AsyncSession],
    identity: TokenIdentity,
    audit: InMemoryAudit,
    task_id: str,
) -> None:
    """門番の記録（許可・拒否）を audit_events に残す。どのトークンのどのタスクかを添える。"""
    if not audit.entries:
        return
    async with session_factory() as session, session.begin():
        repo = AuditRepository(session)
        for entry in audit.entries:
            await repo.record(
                actor=f"a2a:{identity.token_id}",
                action="a2a.tool",
                target=identity.team,
                decision=entry["decision"],
                detail={**entry, "a2a_task_id": task_id},
            )
