"""ADK App + GatewayPlugin を ScriptedLlm で決定的に検証する。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from speclino.adapters.llm.scripted import ScriptedLlm, call, text
from speclino.agent.app import Investigator
from speclino.agent.gateway import ToolRegistry
from speclino.agent.permissions import AgentPermissions
from speclino.services.cost_guard import CostGuard, InMemoryLedger


def build(
    steps: list[list[Any]], perms: AgentPermissions | None = None
) -> tuple[Investigator, list[str]]:
    ScriptedLlm.reset()
    executed: list[str] = []
    registry = ToolRegistry()
    registry.register(
        "repo_search",
        lambda a: executed.append(f"repo_search:{a.get('query')}") or {"hits": ["a.py"]},
    )
    registry.register("repo_write", lambda a: executed.append("repo_write") or {"ok": True})

    inv_holder: dict[str, Investigator] = {}

    def repo_search(query: str) -> dict[str, Any]:
        """リポジトリを検索する。"""
        from speclino.agent.gateway import ToolCall

        return inv_holder["inv"].gateway.invoke(ToolCall("repo_search", {"query": query})).to_dict()

    def repo_write(path: str, content: str) -> dict[str, Any]:
        """（ハニーポット）ファイルを書き込む。"""
        from speclino.agent.gateway import ToolCall

        return (
            inv_holder["inv"]
            .gateway.invoke(ToolCall("repo_write", {"path": path, "content": content}))
            .to_dict()
        )

    inv = Investigator(
        permissions=perms or AgentPermissions(),
        registry=registry,
        tool_fns=[repo_search, repo_write],
        instruction="調査せよ",
        model=ScriptedLlm(steps=steps),
    )
    inv_holder["inv"] = inv
    return inv, executed


async def test_allowed_tool_call_flows_through_gateway() -> None:
    inv, executed = build([[call("repo_search", query="restore")], [text("完了")]])
    log = await inv.run("R-CANCEL-04 を調べて")
    assert executed == ["repo_search:restore"]
    assert log.final_text == "完了"
    assert log.budget["tool_calls"] == 1
    assert [e["decision"] for e in inv.audit.entries] == ["allowed"]
    assert log.llm_calls == 2 and log.input_tokens == 2000


async def test_honeypot_is_denied_by_plugin_and_never_executed() -> None:
    inv, executed = build([[call("repo_write", path="a.py", content="x")], [text("done")]])
    log = await inv.run("書き込んで")
    assert executed == []
    assert log.denied == [{"tool": "repo_write", "reason": "honeypot"}]
    assert inv.audit.entries[0]["decision"] == "denied"
    assert inv.audit.entries[0]["injection_suspected"] is True
    # 拒否結果がモデルへ返され、次のターンへ進む
    assert any(e["function_responses"] for e in log.events)


async def test_tool_budget_stops_investigation() -> None:
    steps = [[call("repo_search", query=str(i))] for i in range(5)] + [[text("end")]]
    inv, executed = build(steps, AgentPermissions(max_tool_calls=2, max_llm_calls=40))
    log = await inv.run("たくさん検索して")
    assert executed == ["repo_search:0", "repo_search:1"]
    assert log.budget["stop_reason"] and "上限" in log.budget["stop_reason"]
    assert "[budget]" in log.final_text


async def test_llm_call_limit_stops_investigation() -> None:
    steps = [[call("repo_search", query=str(i))] for i in range(10)] + [[text("end")]]
    inv, executed = build(steps, AgentPermissions(max_tool_calls=20, max_llm_calls=3))
    log = await inv.run("x")
    assert len(executed) <= 3
    assert "[budget]" in log.final_text


async def test_cost_guard_blocks_when_daily_limit_reached() -> None:
    ledger = InMemoryLedger(rows=[])
    guard = CostGuard(ledger, daily_limit_usd=Decimal("1.00"), per_run_limit_usd=Decimal("0.50"))
    today = date(2026, 9, 22)
    ok, _ = await guard.can_start(today)
    assert ok
    usd = await guard.record(today, "run-1", "gemini-3.8-flash", 400_000, 100_000)
    assert usd == Decimal("0.675")
    ok, reason = await guard.can_start(today)
    assert not ok and "費用枠" in reason


@pytest.mark.live
async def test_live_gemini_one_turn() -> None:  # pragma: no cover
    inv, _ = build([])
    inv = Investigator(
        inv.permissions, inv.registry, [], "1 文で挨拶して", model="gemini-3.8-flash"
    )
    log = await inv.run("こんにちは")
    assert log.final_text
