"""ToolGateway: 拒否は実行より先に起き、監査に残る。"""

from __future__ import annotations

from speclino.agent.budget import BudgetState
from speclino.agent.gateway import InMemoryAudit, ToolCall, ToolGateway, ToolRegistry
from speclino.agent.permissions import AgentPermissions


def make(
    perms: AgentPermissions | None = None, max_calls: int = 20
) -> tuple[ToolGateway, list[str], InMemoryAudit]:
    executed: list[str] = []
    registry = ToolRegistry()
    registry.register("repo_search", lambda args: executed.append("repo_search") or {"hits": []})
    registry.register("repo_write", lambda args: executed.append("repo_write") or {"written": True})
    audit = InMemoryAudit()
    gw = ToolGateway(perms or AgentPermissions(), registry, audit, BudgetState(max_calls, 600))
    return gw, executed, audit


def test_not_permitted_is_denied_before_execution_and_audited() -> None:
    gw, executed, audit = make()
    res = gw.invoke(ToolCall("test_run", {"ids": ["t1"]}))
    assert not res.ok and res.denied_reason == "not_permitted"
    assert executed == []
    assert audit.entries == [
        {
            "decision": "denied",
            "tool": "test_run",
            "args_hash": audit.entries[0]["args_hash"],
            "reason": "not_permitted",
            "injection_suspected": False,
        }
    ]


def test_honeypot_is_denied_and_flagged_as_injection() -> None:
    gw, executed, audit = make()
    res = gw.invoke(ToolCall("repo_write", {"path": "x"}))
    assert not res.ok and executed == []
    assert audit.entries[0]["injection_suspected"] is True


def test_permitted_executes_exactly_once_with_audit() -> None:
    gw, executed, audit = make()
    res = gw.invoke(ToolCall("repo_search", {"query": "restore"}))
    assert res.ok and executed == ["repo_search"]
    assert [e["decision"] for e in audit.entries] == ["allowed"]
    assert gw.budget.tool_calls == 1


def test_budget_exhaustion_denies_further_calls() -> None:
    gw, executed, audit = make(max_calls=2)
    for _ in range(3):
        gw.invoke(ToolCall("repo_search", {}))
    assert executed == ["repo_search", "repo_search"]
    assert audit.entries[-1]["decision"] == "denied"
    assert "上限" in audit.entries[-1]["reason"]


def test_unknown_tool_is_not_an_exception() -> None:
    gw, _, _ = make(AgentPermissions(allowed_tools=frozenset({"ghost"})))
    res = gw.invoke(ToolCall("ghost", {}))
    assert not res.ok and res.payload["error"] == "unknown_tool"
