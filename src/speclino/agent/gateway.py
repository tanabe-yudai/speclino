"""ToolGateway: すべてのツール実行の入口（FR 014 / AC-012、自己検証 R-TOOL-01/02 の対象）。

処理順は「権限判定 → 拒否なら監査して denied を返す → 許可なら実行して監査」。
この関数は SpecLino 自身の解析対象なので、対応構文（if / 早期 return / 直接呼出し）だけで書く。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from speclino.agent.budget import BudgetState
from speclino.agent.permissions import AgentPermissions

MAX_RESULT_CHARS = 20_000


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, Any]
    denied_reason: str | None = None

    @staticmethod
    def denied(reason: str) -> ToolResult:
        return ToolResult(
            ok=False, payload={"error": "denied", "reason": reason}, denied_reason=reason
        )

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class AuditSink(Protocol):
    def record_denied(self, call: ToolCall, reason: str, suspected_injection: bool) -> None: ...

    def record_allowed(self, call: ToolCall, result: ToolResult, duration_ms: int) -> None: ...


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolRegistry:
    """ツール名 → 実装。実装は決定的な Python 関数で、Evidence を返す。"""

    tools: dict[str, ToolFn] = field(default_factory=dict)

    def register(self, name: str, fn: ToolFn) -> None:
        self.tools[name] = fn

    def execute(self, call: ToolCall) -> ToolResult:
        fn = self.tools.get(call.tool)
        if fn is None:
            return ToolResult(ok=False, payload={"error": "unknown_tool", "tool": call.tool})
        payload = fn(call.args)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            payload = {"truncated": True, "preview": text[:MAX_RESULT_CHARS]}
        return ToolResult(ok=True, payload=payload)


@dataclass
class InMemoryAudit:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record_denied(self, call: ToolCall, reason: str, suspected_injection: bool) -> None:
        self.entries.append(
            {
                "decision": "denied",
                "tool": call.tool,
                "args_hash": args_hash(call.args),
                "reason": reason,
                "injection_suspected": suspected_injection,
            }
        )

    def record_allowed(self, call: ToolCall, result: ToolResult, duration_ms: int) -> None:
        self.entries.append(
            {
                "decision": "allowed",
                "tool": call.tool,
                "args_hash": args_hash(call.args),
                "result_hash": hashlib.sha256(
                    json.dumps(result.payload, sort_keys=True, default=str).encode()
                ).hexdigest()[:16],
                "duration_ms": duration_ms,
            }
        )


def args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


class ToolGateway:
    def __init__(
        self,
        permissions: AgentPermissions,
        registry: ToolRegistry,
        audit: AuditSink,
        budget: BudgetState,
    ) -> None:
        self.permissions = permissions
        self.registry = registry
        self.audit = audit
        self.budget = budget

    def deny_reason(self, call: ToolCall) -> str | None:
        """拒否理由。None なら許可。順序: ハニーポット → 権限 → 予算。"""
        if self.permissions.is_honeypot(call.tool):
            return "honeypot"
        if not self.permissions.allows(call.tool):
            return "not_permitted"
        exhausted = self.budget.exhausted_reason()
        if exhausted is not None:
            return exhausted
        return None

    def invoke(self, call: ToolCall) -> ToolResult:
        """R-TOOL-01: 許可されなければ実行せず監査して denied。R-TOOL-02: 許可なら 1 回だけ実行。

        SpecLino 自身の解析対象（specs/SPEC-TOOL-001.md）。対応構文だけで書く。
        """
        permitted = self.permissions.allows(call.tool)
        if not permitted:
            self.audit.record_denied(call, "not_permitted", self.permissions.is_honeypot(call.tool))
            return ToolResult.denied("not_permitted")
        budget_exhausted = self.budget.is_exhausted()
        if budget_exhausted:
            self.audit.record_denied(call, self.budget.exhausted_reason() or "budget", False)
            return ToolResult.denied(self.budget.exhausted_reason() or "budget")
        self.budget.charge()
        started = time.monotonic()
        result = self.registry.execute(call)
        self.audit.record_allowed(call, result, int((time.monotonic() - started) * 1000))
        return result
