"""ADK の App / Runner を組み立て、調査を 1 実行として回す（永続化・中断再開は使わない）。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.models.base_llm import BaseLlm
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from speclino.agent.budget import BudgetState
from speclino.agent.gateway import InMemoryAudit, ToolGateway, ToolRegistry
from speclino.agent.permissions import AgentPermissions
from speclino.agent.plugins.gateway_plugin import GatewayPlugin

DEFAULT_MODEL = "gemini-3.8-flash"  # 明示 ID で固定（-latest は FR 016 に反する）
APP_NAME = "speclino-investigator"


@dataclass
class InvestigationLog:
    events: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    budget: dict[str, Any] = field(default_factory=dict)
    denied: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Investigator:
    """ToolGateway と ADK App を束ねる。tool_fns は ADK に渡す薄いラッパ（本体は gateway）。"""

    permissions: AgentPermissions
    registry: ToolRegistry
    tool_fns: list[Callable[..., Any]]
    instruction: str
    model: str | BaseLlm = DEFAULT_MODEL
    audit: InMemoryAudit = field(default_factory=InMemoryAudit)
    extra_plugins: list[BasePlugin] = field(default_factory=list)
    max_output_tokens: int | None = None  # 1 回の LLM 呼出しの出力の上限（None はモデルの既定）

    def __post_init__(self) -> None:
        self.budget = BudgetState(self.permissions.max_tool_calls, self.permissions.max_seconds)
        self.gateway = ToolGateway(self.permissions, self.registry, self.audit, self.budget)
        self.plugin = GatewayPlugin(self.gateway)
        agent = LlmAgent(
            name="investigator",
            model=self.model,
            instruction=self.instruction,
            tools=list(self.tool_fns),
            generate_content_config=(
                types.GenerateContentConfig(max_output_tokens=self.max_output_tokens)
                if self.max_output_tokens is not None
                else None
            ),
        )
        self.app = App(name=APP_NAME, root_agent=agent, plugins=[self.plugin, *self.extra_plugins])

    async def run(self, prompt: str, user_id: str = "speclino") -> InvestigationLog:
        session_service = InMemorySessionService()
        session = await session_service.create_session(app_name=APP_NAME, user_id=user_id)
        runner = Runner(app=self.app, session_service=session_service)
        log = InvestigationLog()
        message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        run_config = RunConfig(max_llm_calls=self.permissions.max_llm_calls)

        async def _consume() -> None:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=message,
                run_config=run_config,
            ):
                log.events.append(_event_to_dict(event))
                if event.usage_metadata is not None:
                    log.input_tokens += event.usage_metadata.prompt_token_count or 0
                    # 課金される出力は候補と思考の合計
                    log.output_tokens += (event.usage_metadata.candidates_token_count or 0) + (
                        event.usage_metadata.thoughts_token_count or 0
                    )
                    log.llm_calls += 1
                if event.content and event.content.parts:
                    texts = [p.text for p in event.content.parts if p.text]
                    if texts and event.author != "user":
                        log.final_text = "\n".join(texts)

        try:
            await asyncio.wait_for(_consume(), timeout=self.permissions.max_seconds)
        except TimeoutError:
            self.budget.stop(f"経過時間上限 {self.permissions.max_seconds} 秒に到達")
        finally:
            await runner.close()
        log.budget = self.budget.snapshot()
        log.denied = list(self.plugin.denied)
        return log


def _event_to_dict(event: Any) -> dict[str, Any]:
    calls = []
    responses = []
    texts = []
    if event.content and event.content.parts:
        for p in event.content.parts:
            if p.function_call is not None:
                calls.append(
                    {"name": p.function_call.name, "args": dict(p.function_call.args or {})}
                )
            if p.function_response is not None:
                responses.append({"name": p.function_response.name})
            if p.text:
                texts.append(p.text)
    return {
        "author": event.author,
        "function_calls": calls,
        "function_responses": responses,
        "text": "\n".join(texts),
    }
