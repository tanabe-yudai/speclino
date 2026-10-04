"""spec_review: 曖昧な点を聞き返し、候補・診断・ぶつかり・修正案を返す。答えは本文に混ぜない。"""

from __future__ import annotations

import asyncio
import copy
from datetime import date, timedelta
from typing import Any

import pytest

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.agent.consultant import spec_review
from speclino.agent.consultant.spec_review import (
    MAX_REVIEW_ROUNDS,
    render_revision_diff,
    run_spec_review,
)
from speclino.agent.consultant.tools import ConsultTools
from speclino.ports.text_llm import JsonGeneration
from speclino.services.cost_guard import CostGuard
from speclino.services.spec_pr_bot import MAX_SPEC_CHARS
from speclino.spec.loader import content_hash
from tests.unit.agent.consultant.fakes import (
    ORDERS,
    LedgerBudget,
    demo_context,
    dumps,
    fake_call,
    fake_deps,
    priced_guard,
    stub_fetch_spec_context,
)

SPEC_MD = (
    (ORDERS / "specs" / "orders-cancel.md")
    .read_text(encoding="utf-8")
    .replace("でなければ403を返す。", "でなければすぐに403を返す。")
)
assert "すぐに" in SPEC_MD

VALID_RULE: dict[str, Any] = {
    "id": "R-CANCEL-04",
    "requirement_id": "R-CANCEL-04",
    "title": "在庫復元を 1 回呼ぶ",
    "modality": "must",
    "claim_scope": "command_invocation",
    "applicability": {
        "op": "eq",
        "left": {"op": "ref", "path": "before.order.status"},
        "right": {"op": "const", "type": "OrderStatus", "value": "PENDING"},
    },
    "assertion": {
        "kind": "event_requirement",
        "event_kind": "call",
        "operation_or_target": "inventory_restore_command",
        "phase": "invoked",
        "count": {"operator": "eq", "value": 1},
    },
}
AMBIGUOUS = dumps(
    {
        "rules": [VALID_RULE],
        "unextracted": [],
        "ambiguities": [
            {"requirement_id": "R-CANCEL-01", "phrase": "すぐに", "question": "何秒以内？"}
        ],
    }
)
CLEAR = dumps({"rules": [VALID_RULE], "unextracted": [], "ambiguities": []})
REVISION = dumps(
    {
        "edits": [
            {
                "requirement_id": "R-CANCEL-01",
                "before": "すぐに",
                "after": "5 秒以内に",
                "reason": "回答に合わせた",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _stub_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConsultTools, "fetch_spec_context", stub_fetch_spec_context)


class RaisingLlm:
    model = "scripted"
    max_output_tokens = 8192

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        raise RuntimeError("gemini down")


class CancelledLlm(RaisingLlm):
    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        raise asyncio.CancelledError


def _guard() -> CostGuard:
    return priced_guard("3")


def _names(call: Any) -> list[str]:
    return [a["name"] for a in call.updater.artifacts]


async def test_asks_ambiguities_then_returns_candidate_and_diff() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm([AMBIGUOUS, CLEAR, REVISION]), cost_guard=_guard())
    first = fake_call(text=SPEC_MD)
    await run_spec_review(first, deps)
    assert first.updater.calls[-1][0] == "requires_input"  # type: ignore[attr-defined]
    assert "何秒以内？" in first.updater.last_text()  # type: ignore[attr-defined]
    state = first.updater.last_metadata()["speclino"]  # type: ignore[attr-defined]
    assert state["round"] == 1 and state["behavior"] == "cancel_order"
    second = fake_call(text=SPEC_MD, reply="1. 5 秒以内", state=state)
    await run_spec_review(second, deps)
    assert _names(second) == [
        "spec-ir-candidate.json",
        "diagnostics.json",
        "conflicts.json",
        "revision.diff",
        "summary.md",
    ]
    up: Any = second.updater
    added = [ln for ln in up.artifact_text("revision.diff").splitlines() if ln.startswith("+")]
    assert any("でなければ5 秒以内に403を返す" in ln for ln in added)
    assert "人が行います" in up.artifact_text("summary.md")
    assert up.calls[-1][0] == "complete"
    llm: Any = deps.spec_llm
    assert "相手のエージェントの回答" in llm.prompts[1] and "1. 5 秒以内" in llm.prompts[1]
    assert "相手のエージェントの回答" not in llm.prompts[0]
    cand = up.artifact_json("spec-ir-candidate.json")
    assert cand["spec_content_hash"] == content_hash(SPEC_MD)  # 答えは本文に混ざらない
    conflicts = up.artifact_json("conflicts.json")
    assert [c["id"] for c in conflicts["overwrites"]] == ["R-CANCEL-04"]
    # 監査: 語彙の取得と抽出（2 回目の呼出しでは抽出と修正案）が門番を通っている
    assert [e["tool"] for e in second.audit.entries] == [
        "spec_context",
        "spec_extract",
        "spec_extract",
    ]


async def test_stops_asking_after_max_rounds() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm([AMBIGUOUS, AMBIGUOUS, AMBIGUOUS, REVISION]))
    state: dict[str, Any] | None = None
    reply: str | None = None
    for _ in range(MAX_REVIEW_ROUNDS):
        call = fake_call(text=SPEC_MD, reply=reply, state=state)
        await run_spec_review(call, deps)
        up: Any = call.updater
        assert up.calls[-1][0] == "requires_input"
        state, reply = up.last_metadata()["speclino"], "わからない"
    last = fake_call(text=SPEC_MD, reply=reply, state=state)
    await run_spec_review(last, deps)
    up = last.updater
    assert up.calls[-1][0] == "complete"
    assert "何秒以内？" in up.artifact_text("summary.md")  # 残った質問


async def test_too_large_spec_is_refused_before_calling_llm() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm(CLEAR))
    call = fake_call(text="x" * (MAX_SPEC_CHARS + 1))
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert up.calls[-1][0] == "failed" and deps.spec_llm.prompts == []  # type: ignore[union-attr]
    assert f"上限 {MAX_SPEC_CHARS} 文字" in up.last_text()


@pytest.mark.parametrize(("fields", "text"), [({"repo": None}, SPEC_MD), ({}, "  \n"), ({}, "")])
async def test_missing_repo_or_empty_text_fails(fields: dict[str, Any], text: str) -> None:
    deps = fake_deps(llm=ScriptedJsonLlm(CLEAR))
    call = fake_call(text=text, **fields)
    await run_spec_review(call, deps)
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert deps.spec_llm.prompts == []  # type: ignore[union-attr]


async def test_missing_vocabulary_fails() -> None:
    ctx = demo_context()
    ctx["bindings"] = None
    deps = fake_deps(llm=ScriptedJsonLlm(CLEAR), context=ctx)
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert up.calls[-1][0] == "failed" and ".speclino/" in up.last_text()


async def test_cost_guard_stops_revision_but_keeps_extraction() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm([CLEAR, REVISION]), cost_guard=_guard())
    budget = LedgerBudget(allow=1)
    deps.budget = budget
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert "revision.diff" not in _names(call)
    assert "費用" in up.artifact_text("summary.md")
    assert up.calls[-1][0] == "complete" and len(deps.spec_llm.prompts) == 1  # type: ignore[union-attr]
    assert budget.settled == [(budget.reserved[0], 1000, 200)]


async def test_cost_guard_refusing_extraction_fails() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm(CLEAR), cost_guard=_guard())
    deps.budget = LedgerBudget(allow=0)  # 1 回目から断る
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert up.calls[-1][0] == "failed" and "費用ガードにより停止" in up.last_text()
    assert deps.spec_llm.prompts == []  # type: ignore[union-attr]


async def test_llm_exception_settles_reservation_to_zero_and_fails() -> None:
    llm = RaisingLlm()
    deps = fake_deps(llm=llm, cost_guard=_guard())
    budget = LedgerBudget()
    deps.budget = budget
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert budget.settled == [(budget.reserved[0], 0, 0)] and budget.net == 0
    assert _names(call) == []


async def test_cancelled_extraction_releases_reservation() -> None:
    deps = fake_deps(llm=CancelledLlm(), cost_guard=_guard())
    budget = LedgerBudget()
    deps.budget = budget
    call = fake_call(text=SPEC_MD)
    with pytest.raises(asyncio.CancelledError):
        await run_spec_review(call, deps)
    assert budget.settled == [(budget.reserved[0], 0, 0)] and budget.net == 0
    # 取り消しでも門番の監査は残る
    assert [e["tool"] for e in call.audit.entries][-1] == "spec_extract"


async def test_each_reservation_reads_today_once() -> None:
    """予約と精算は同じ日に付ける（日付をまたいでも別の日に精算しない）。"""
    days: list[date] = []

    def today() -> date:
        days.append(date(2026, 10, 4) + timedelta(days=len(days)))
        return days[-1]

    deps = fake_deps(llm=ScriptedJsonLlm([CLEAR, REVISION]), cost_guard=_guard())
    deps.today = today
    budget = LedgerBudget()
    deps.budget = budget
    await run_spec_review(fake_call(text=SPEC_MD), deps)
    assert len(budget.reserved) == 2 and len(days) == 2


async def test_broken_extraction_settles_and_skips_revision() -> None:
    llm = ScriptedJsonLlm(["not json", REVISION])
    deps = fake_deps(llm=llm, cost_guard=_guard())
    budget = LedgerBudget()
    deps.budget = budget
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert up.calls[-1][0] == "failed" and "仕様の抽出結果の形式が不正でした" in up.last_text()
    assert len(llm.prompts) == 1  # 修正案の LLM を呼ばない
    assert budget.settled == [(budget.reserved[0], 1000, 200)]
    assert _names(call) == []


@pytest.mark.parametrize("llm", [ScriptedJsonLlm("not json"), RaisingLlm()])
async def test_broken_llm_fails_without_presenting_partial_result(llm: Any) -> None:
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, fake_deps(llm=llm))
    # 壊れた JSON も例外も失敗にし、空の候補を結果として見せない
    up: Any = call.updater
    assert up.calls[-1][0] == "failed"
    assert _names(call) == []


def _two_behaviors() -> dict[str, Any]:
    ctx = demo_context()
    cancel = ctx["bindings"]["behaviors"]["cancel_order"]
    ctx["bindings"]["behaviors"] = {"orders.cancel": cancel, "orders.refund": copy.copy(cancel)}
    ctx["rules"] = []
    return ctx


async def test_asks_behavior_when_it_cannot_be_inferred() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm(CLEAR), context=_two_behaviors())
    call = fake_call(text=SPEC_MD)
    await run_spec_review(call, deps)
    up: Any = call.updater
    assert up.calls[-1][0] == "requires_input"
    assert "orders.refund" in up.last_text() and deps.spec_llm.prompts == []  # type: ignore[union-attr]
    assert up.last_metadata()["speclino"]["need"] == "behavior"


async def test_behavior_answer_resumes_and_wrong_answers_fail_on_second() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm([CLEAR, REVISION]), context=_two_behaviors())
    first = fake_call(text=SPEC_MD)
    await run_spec_review(first, deps)
    state = first.updater.last_metadata()["speclino"]  # type: ignore[attr-defined]
    ok = fake_call(text=SPEC_MD, reply=" orders.cancel ", state=state)
    await run_spec_review(ok, deps)
    assert ok.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]
    cand = ok.updater.artifact_json("spec-ir-candidate.json")  # type: ignore[attr-defined]
    assert cand["behavior_id"] == "orders.cancel"

    wrong1 = fake_call(text=SPEC_MD, reply="nope", state=state)
    await run_spec_review(wrong1, deps)
    assert wrong1.updater.calls[-1][0] == "requires_input"  # type: ignore[attr-defined]
    state2 = wrong1.updater.last_metadata()["speclino"]  # type: ignore[attr-defined]
    wrong2 = fake_call(text=SPEC_MD, reply="nope", state=state2)
    await run_spec_review(wrong2, deps)
    assert wrong2.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]


async def test_behavior_inferred_from_existing_rule_with_same_path() -> None:
    ctx = _two_behaviors()
    ctx["rules"] = [{"id": "R-X", "behavior_id": "orders.refund", "spec": "specs/refund.md"}]
    deps = fake_deps(llm=ScriptedJsonLlm([CLEAR, REVISION]), context=ctx)
    call = fake_call(text=SPEC_MD, path="specs/refund.md")
    await run_spec_review(call, deps)
    cand = call.updater.artifact_json("spec-ir-candidate.json")  # type: ignore[attr-defined]
    assert cand["behavior_id"] == "orders.refund"


async def test_overlong_prompt_with_answers_is_refused() -> None:
    deps = fake_deps(llm=ScriptedJsonLlm([CLEAR]))
    state = {"round": 1, "behavior": "cancel_order", "questions": [["q"]], "answers": []}
    call = fake_call(text=SPEC_MD, reply="y" * (2 * MAX_SPEC_CHARS), state=state)
    await run_spec_review(call, deps)
    assert call.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert deps.spec_llm.prompts == []  # type: ignore[union-attr]


def test_render_revision_diff_skips_edits_whose_before_is_missing() -> None:
    diff, skipped = render_revision_diff(
        "A すぐに B\n",
        [
            {"requirement_id": "R", "before": "すぐに", "after": "5 秒以内に", "reason": ""},
            {"requirement_id": "R", "before": "無い文", "after": "x", "reason": ""},
        ],
    )
    assert "+A 5 秒以内に B" in diff and len(skipped) == 1


def test_module_exports_schema() -> None:
    assert spec_review.REVISION_SCHEMA["properties"]["edits"]["type"] == "array"
