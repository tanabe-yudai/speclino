"""ADK Plugin: ツール実行前に ToolGateway の判定を強制する（二重の防御）。

ツール本体も gateway.invoke を経由するが、Plugin が先に走り、拒否なら dict を返して
ADK 側のツール実行をスキップする。呼出しの記録は gateway の監査に集約する。
"""

from __future__ import annotations

from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from speclino.agent.gateway import ToolCall, ToolGateway


class GatewayPlugin(BasePlugin):
    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(name="speclino_gateway")
        self.gateway = gateway
        self.llm_calls = 0
        self.denied: list[dict[str, Any]] = []

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any] | None:
        call = ToolCall(tool.name, dict(tool_args))
        reason = self.gateway.deny_reason(call)
        if reason is None:
            return None
        # 拒否は gateway.invoke と同じ監査経路に残す（Plugin だけで判定を完結させない）
        result = self.gateway.invoke(call)
        self.denied.append({"tool": tool.name, "reason": reason})
        return result.to_dict()

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        self.llm_calls += 1
        reason = self.gateway.budget.exhausted_reason()
        if reason is None and self.llm_calls <= self.gateway.permissions.max_llm_calls:
            return None
        stop = reason or f"LLM 呼出し上限 {self.gateway.permissions.max_llm_calls} 回に到達"
        self.gateway.budget.stop(stop)
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=f"[budget] 調査を終了: {stop}")],
            ),
            turn_complete=True,
        )
