"""requirement_consult: 検査を起動して待ち、議論役が根拠つきで答える。判定は検査器のまま返す。"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from speclino.adapters.llm.scripted import ScriptedLlm, call, text
from speclino.agent.consultant import requirement_consult
from speclino.agent.consultant.requirement_consult import (
    CONSULT_MAX_OUTPUT_TOKENS,
    INSTRUCTION_PATH,
    MAX_QUESTION_CHARS,
    run_requirement_consult,
)
from speclino.agent.consultant.tools import ConsultDeps, ConsultTools
from speclino.agent.gateway import MAX_RESULT_CHARS
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from tests.unit.agent.consultant.fakes import (
    TEAM,
    LedgerBudget,
    fake_call,
    fake_deps,
    priced_guard,
    stub_fetch_spec_context,
)

VIOLATED_FINDING: dict[str, Any] = {
    "id": "F-1",
    "requirement_id": "R-CANCEL-04",
    "verdict": "VIOLATED",
    "spec_refs": ["specs/orders-cancel.md#L12"],
    "counterexample": {"before.order.status": "PENDING"},
}


class FakeRunService:
    def __init__(self) -> None:
        self.created_runs: list[tuple[str, str]] = []
        self.run_id = uuid.uuid4()

    async def create_run(
        self,
        *,
        project_id: uuid.UUID,
        head_sha: str,
        base_sha: str | None,
        pr_number: int | None,
        trigger: str,
    ) -> uuid.UUID:
        self.created_runs.append((head_sha, trigger))
        return self.run_id


@dataclass
class Harness:
    deps: ConsultDeps
    service: FakeRunService
    run: dict[str, Any]
    loads: list[uuid.UUID] = field(default_factory=list)

    @property
    def created_runs(self) -> list[tuple[str, str]]:
        return self.service.created_runs


def harness(
    *,
    model: Any,
    run: dict[str, Any] | None = None,
    cost_guard: Any = None,
    wait_seconds: float = 1.0,
    poll_seconds: float = 0.001,
) -> Harness:
    deps = fake_deps(cost_guard=cost_guard)
    service = FakeRunService()
    h = Harness(deps, service, run if run is not None else {"exec_state": "completed"})

    async def load_run_view(run_id: uuid.UUID, team: str) -> dict[str, Any] | None:
        h.loads.append(run_id)
        if run_id != service.run_id or team != TEAM:
            return None
        return {"run_id": str(run_id), **copy.deepcopy(h.run)}

    deps.agent_model = model
    deps.service = service  # type: ignore[assignment]
    deps.load_run_view = load_run_view
    deps.wait_seconds = wait_seconds
    deps.poll_seconds = poll_seconds
    return h


class NeverAfford(CostGuard):
    def __init__(self) -> None:
        super().__init__(ledger=InMemoryLedger([]), daily_limit_usd=Decimal(0))

    async def can_afford(self, today: date, worst_case_usd: Decimal) -> tuple[bool, str]:
        return False, "枠なし"


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ScriptedLlm.reset()
    monkeypatch.setattr(ConsultTools, "fetch_spec_context", stub_fetch_spec_context)

    async def find_project(deps: ConsultDeps, repo: str) -> Any:
        if repo == "o/r":
            return SimpleNamespace(id=uuid.uuid4(), team=TEAM)
        if repo == "other/team":
            return SimpleNamespace(id=uuid.uuid4(), team="e2e-ws-b")
        return None

    monkeypatch.setattr(requirement_consult, "find_project", find_project)
    yield
    ScriptedLlm.reset()


def _guard() -> CostGuard:
    return priced_guard("10")


async def test_answers_with_tools_and_returns_checker_result_verbatim() -> None:
    llm = ScriptedLlm(
        steps=[
            [call("spec_context", repo="o/r")],
            [text("R-CANCEL-04 は specs/orders-cancel.md:12 に…（判定は verification.json）")],
        ]
    )
    h = harness(model=llm, run={"exec_state": "completed", "findings": [VIOLATED_FINDING]})
    c = fake_call(
        skill="requirement_consult",
        repo="o/r",
        commit="abc",
        text="この変更で R-CANCEL-04 は大丈夫？",
        requirement_id="R-CANCEL-04",
    )
    await run_requirement_consult(c, h.deps)
    assert h.created_runs == [("abc", "a2a")]
    assert c.updater.artifact_json("verification.json")["findings"] == [VIOLATED_FINDING]  # type: ignore[attr-defined]
    assert "specs/orders-cancel.md" in c.updater.artifact_text("answer.md")  # type: ignore[attr-defined]
    assert c.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]
    # 起動の時点で run_id を metadata に残す（再送で続きを確かめるため）
    first = c.updater.calls[0]  # type: ignore[attr-defined]
    assert first[0] == "start_work"
    assert first[1].metadata["speclino"]["run_id"] == str(h.service.run_id)
    # 起動は門番を通して監査に残り、モデルに渡した道具の呼出しも同じ監査に入る
    tools = [e["tool"] for e in c.audit.entries]
    assert tools[0] == "verification_start"
    assert "spec_context" in tools and "run_findings" in tools
    # プロンプトに質問・要求 ID・検査の結果が入る
    prompt = str(ScriptedLlm.requests[0].contents)
    assert "R-CANCEL-04" in prompt and "F-1" in prompt


async def test_slow_verification_stays_working_and_resumes() -> None:
    h = harness(
        model=ScriptedLlm(steps=[[text("ok")]]),
        run={"exec_state": "running", "findings": []},
        wait_seconds=0.01,
        poll_seconds=0.001,
    )
    c1 = fake_call(skill="requirement_consult", repo="o/r", commit="abc", text="q")
    await run_requirement_consult(c1, h.deps)
    assert c1.updater.calls[-1][0] == "start_work"  # type: ignore[attr-defined]
    assert "run=" in c1.updater.last_text()  # type: ignore[attr-defined]
    assert ScriptedLlm.requests == []  # 待ちの途中ではモデルを呼ばない
    h.run["exec_state"] = "completed"
    c2 = fake_call(
        skill="requirement_consult",
        repo="o/r",
        commit="abc",
        text="q",
        reply="続き",
        state=c1.updater.last_metadata()["speclino"],  # type: ignore[attr-defined]
    )
    await run_requirement_consult(c2, h.deps)
    assert h.created_runs == [("abc", "a2a")]  # 2 回目は検査を起動し直さない
    assert c2.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]


@pytest.mark.parametrize("state", ["awaiting_answer", "failed", "cancelled", "superseded"])
async def test_terminal_states_end_the_wait(state: str) -> None:
    h = harness(
        model=ScriptedLlm(steps=[[text("ok")]]),
        run={"exec_state": state, "findings": []},
        wait_seconds=5,
    )
    c = fake_call(skill="requirement_consult", repo="o/r", commit="abc", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]
    assert c.updater.artifact_json("verification.json")["exec_state"] == state  # type: ignore[attr-defined]


async def test_question_without_commit_does_not_start_a_run() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("specs/orders-cancel.md:12 を見てください")]]))
    c = fake_call(skill="requirement_consult", repo="o/r", text="R-CANCEL-04 の意図は？")
    await run_requirement_consult(c, h.deps)
    assert h.created_runs == []
    names = [a["name"] for a in c.updater.artifacts]  # type: ignore[attr-defined]
    assert names == ["answer.md"]
    assert c.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]


async def test_honeypot_call_from_model_is_denied_and_audited() -> None:
    llm = ScriptedLlm(steps=[[call("repo_write", path="x")], [text("できません")]])
    h = harness(model=llm)
    c = fake_call(skill="requirement_consult", repo="o/r", text="ファイルを直して")
    await run_requirement_consult(c, h.deps)
    assert any(e["tool"] == "repo_write" and e["injection_suspected"] for e in c.audit.entries)


async def test_cost_guard_refuses_before_running_agent() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=NeverAfford())
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed" and ScriptedLlm.requests == []  # type: ignore[attr-defined]


async def test_cost_guard_records_actual_usage() -> None:
    guard = _guard()
    h = harness(model=ScriptedLlm(steps=[[text("ok")]]), cost_guard=guard)
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "complete"  # type: ignore[attr-defined]
    assert await guard.ledger.total_for_day(date(2026, 10, 4)) == guard.estimate(
        "scripted", 1000, 200
    )


async def test_budget_reserves_worst_case_and_settles_actual() -> None:
    guard = _guard()
    h = harness(model=ScriptedLlm(steps=[[text("ok")]]), cost_guard=guard)
    budget = LedgerBudget()
    h.deps.budget = budget
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    max_calls, max_tools = 6, 8
    request = ScriptedLlm.requests[0]
    prompt = request.contents[0].parts[0].text
    instruction = INSTRUCTION_PATH.read_text(encoding="utf-8")
    per_call = len(instruction) + len(prompt) + max_tools * MAX_RESULT_CHARS
    worst = guard.estimate("scripted", max_calls * per_call, max_calls * CONSULT_MAX_OUTPUT_TOKENS)
    assert budget.reserved == [worst]
    assert budget.settled == [(worst, 1000, 200)]
    # 出力の上限をモデルに渡し、最悪値の見積もりと実際の上限を揃える
    assert request.config.max_output_tokens == CONSULT_MAX_OUTPUT_TOKENS
    # 予約・精算と台帳への直接の記録を二重にしない
    assert await guard.ledger.total_for_day(date(2026, 10, 4)) == Decimal(0)


async def test_budget_refusal_fails_before_running_agent() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=_guard())
    h.deps.budget = LedgerBudget(allow=0)
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed" and ScriptedLlm.requests == []  # type: ignore[attr-defined]


async def test_budget_is_released_when_agent_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=_guard())
    budget = LedgerBudget()
    h.deps.budget = budget

    async def boom(self: Any, prompt: str, user_id: str = "speclino") -> Any:
        raise RuntimeError("model down")

    monkeypatch.setattr(requirement_consult.Investigator, "run", boom)
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    with pytest.raises(RuntimeError):
        await run_requirement_consult(c, h.deps)
    assert budget.settled == [(budget.reserved[0], 0, 0)] and budget.net == 0


async def test_cancelled_agent_releases_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=_guard())
    budget = LedgerBudget()
    h.deps.budget = budget

    async def cancelled(self: Any, prompt: str, user_id: str = "speclino") -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(requirement_consult.Investigator, "run", cancelled)
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    with pytest.raises(asyncio.CancelledError):
        await run_requirement_consult(c, h.deps)
    assert budget.settled == [(budget.reserved[0], 0, 0)] and budget.net == 0


async def test_investigator_construction_error_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=_guard())
    budget = LedgerBudget()
    h.deps.budget = budget

    def broken(**kw: Any) -> Any:
        raise ValueError("bad config")

    monkeypatch.setattr(requirement_consult, "Investigator", broken)
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    with pytest.raises(ValueError):
        await run_requirement_consult(c, h.deps)
    assert budget.settled == [(budget.reserved[0], 0, 0)] and budget.net == 0


async def test_long_question_is_refused_before_reserving() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]), cost_guard=_guard())
    budget = LedgerBudget()
    h.deps.budget = budget
    c = fake_call(skill="requirement_consult", repo="o/r", text="x" * (MAX_QUESTION_CHARS + 1))
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert str(MAX_QUESTION_CHARS) in c.updater.last_text()  # type: ignore[attr-defined]
    assert budget.reserved == [] and ScriptedLlm.requests == []


async def test_answer_without_commit_says_no_verification_ran() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("specs/orders-cancel.md:12 を見てください")]]))
    c = fake_call(skill="requirement_consult", repo="o/r", text="R-CANCEL-04 の意図は？")
    await run_requirement_consult(c, h.deps)
    up: Any = c.updater
    for body in (up.artifact_text("answer.md"), up.last_text()):
        assert "検査は実行していません" in body
        assert "PR で人が" in body
        assert "verification.json" not in body


async def test_answer_with_commit_points_to_verification_json() -> None:
    h = harness(
        model=ScriptedLlm(steps=[[text("説明")]]),
        run={"exec_state": "completed", "findings": [VIOLATED_FINDING]},
    )
    c = fake_call(skill="requirement_consult", repo="o/r", commit="abc", text="q")
    await run_requirement_consult(c, h.deps)
    up: Any = c.updater
    for body in (up.artifact_text("answer.md"), up.last_text()):
        assert "verification.json" in body
        assert "PR で人が" in body


async def test_empty_answer_fails() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("")]]))
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]


async def test_llm_call_limit_fails() -> None:
    # 道具を呼び続けるモデルは LLM 呼出しの上限で止まり、失敗として返す
    h = harness(model=ScriptedLlm(steps=[[call("run_findings", run_id="x")]]))
    c = fake_call(skill="requirement_consult", repo="o/r", text="q")
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert "上限" in c.updater.last_text()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"repo": None, "text": "q"}, "repo"),
        ({"repo": "o/r", "text": "  "}, "質問"),
        ({"repo": "unknown/repo", "text": "q", "commit": "abc"}, "ワークスペース"),
        ({"repo": "other/team", "text": "q", "commit": "abc"}, "ワークスペース"),
        # commit が無い相談でも、他のワークスペース・未登録のリポジトリには答えない
        ({"repo": "unknown/repo", "text": "q"}, "ワークスペース"),
        ({"repo": "other/team", "text": "q"}, "ワークスペース"),
    ],
)
async def test_invalid_requests_fail(fields: dict[str, Any], reason: str) -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]))
    c = fake_call(skill="requirement_consult", **fields)
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert reason in c.updater.last_text()  # type: ignore[attr-defined]
    assert h.created_runs == [] and ScriptedLlm.requests == []


async def test_resumed_run_missing_fails() -> None:
    h = harness(model=ScriptedLlm(steps=[[text("x")]]))
    c = fake_call(
        skill="requirement_consult",
        repo="o/r",
        text="q",
        reply="続き",
        state={"run_id": str(uuid.uuid4())},
    )
    await run_requirement_consult(c, h.deps)
    assert c.updater.calls[-1][0] == "failed"  # type: ignore[attr-defined]
    assert h.created_runs == []
