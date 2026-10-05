"""A2A の spec_suggest（S67）: 本文の書き換えの提案と要約だけを返す。IR の成果物を出さない。"""

from __future__ import annotations

from typing import Any

from speclino.a2a_server.card import build_agent_card
from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.agent.consultant.spec_suggest import run_spec_suggest
from tests.unit.agent.consultant.fakes import LedgerBudget, fake_call, fake_deps, priced_guard
from tests.unit.services.suggest_samples import BAD, FULL, RESPONSE


def _deps(llm: Any, budget: LedgerBudget | None) -> Any:
    deps = fake_deps(llm=llm, cost_guard=priced_guard("10"))
    deps.budget = budget
    return deps


def _names(call: Any) -> list[str]:
    return [a["name"] for a in call.updater.artifacts]


async def test_returns_only_suggestions_and_summary() -> None:
    llm = ScriptedJsonLlm(RESPONSE)
    budget = LedgerBudget()
    call = fake_call(skill="spec_suggest", text=BAD, repo=None)
    await run_spec_suggest(call, _deps(llm, budget))
    assert call.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]
    assert _names(call) == ["suggestions.json", "summary.md"]
    [s] = call.updater.artifact_json("suggestions.json")  # type: ignore[attr-defined]
    # DataPart（protobuf の Struct）を通ると数は float になる
    start, end = int(s["start"]), int(s["end"])
    assert BAD[:start] + s["replacement"] + BAD[end:] == FULL
    assert s["after"]["compiles"] is True
    summary = call.updater.artifact_text("summary.md")  # type: ignore[attr-defined]
    assert "今の本文のエラー: 2 件" in summary
    assert "判定ではありません" in summary
    # 予約した最悪の費用は実費に精算され、門番の監査に残る
    assert len(budget.reserved) == 1 and budget.settled[0][1:] == llm.usage
    [entry] = [e for e in call.audit.entries if e["tool"] == "spec_suggest"]
    # 監査には件数だけを残し、本文や置換の断片を残さない
    assert "注文" not in str(entry) and "OrderStatus" not in str(entry)


async def test_budget_refusal_fails_without_calling_the_model() -> None:
    llm = ScriptedJsonLlm(RESPONSE)
    call = fake_call(skill="spec_suggest", text=BAD, repo=None)
    await run_spec_suggest(call, _deps(llm, LedgerBudget(allow=0)))
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert "費用" in call.updater.last_text()  # type: ignore[attr-defined]
    assert llm.prompts == []
    assert call.updater.artifacts == []  # type: ignore[attr-defined]


async def test_without_budget_fails_closed() -> None:
    llm = ScriptedJsonLlm(RESPONSE)
    call = fake_call(skill="spec_suggest", text=BAD, repo=None)
    await run_spec_suggest(call, _deps(llm, None))
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert llm.prompts == []


async def test_empty_text_fails() -> None:
    call = fake_call(skill="spec_suggest", text="  ", repo=None)
    await run_spec_suggest(call, _deps(ScriptedJsonLlm(RESPONSE), LedgerBudget()))
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]


def test_agent_card_lists_spec_suggest() -> None:
    card = build_agent_card("https://example.test")
    assert "spec_suggest" in {s.id for s in card.skills}
