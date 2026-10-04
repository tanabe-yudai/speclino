"""議論役のツールは門番を通り、他のワークスペースの run は「無い」と同じ応答になる。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from speclino.agent import consultant
from speclino.agent.budget import BudgetState
from speclino.agent.consultant.tools import (
    CONSULT_TOOLS,
    ConsultDeps,
    ConsultTools,
    consult_permissions,
    guarded,
)
from speclino.agent.gateway import MAX_RESULT_CHARS, InMemoryAudit, ToolGateway, ToolRegistry
from speclino.domain.a2a import TokenIdentity
from speclino.services.mcp_context import ContextUnavailable

RUN_ID = uuid4()


def _gateway() -> ToolGateway:
    perms = consult_permissions()
    return ToolGateway(
        perms, ToolRegistry(), InMemoryAudit(), BudgetState(perms.max_tool_calls, perms.max_seconds)
    )


async def _async(value: dict[str, Any]) -> dict[str, Any]:
    return value


def _deps(run_team: str | None = None, context_source: Any = None) -> ConsultDeps:
    async def load_run_view(run_id: uuid.UUID, workspace: str) -> dict[str, Any] | None:
        if run_team != workspace:
            return None
        return {"run_id": str(run_id), "findings": [{"id": "f1"}]}

    return ConsultDeps(
        session_factory=MagicMock(),
        context_source=context_source or MagicMock(),
        service=MagicMock(),
        cost_guard=None,
        spec_llm=None,
        agent_model="scripted",
        today=lambda: date(2026, 10, 4),
        load_run_view=load_run_view,
        record_supplement=AsyncMock(),
    )


def test_permissions_allow_only_consult_tools() -> None:
    perms = consult_permissions()
    assert perms.allowed_tools == CONSULT_TOOLS
    assert perms.test_run_enabled is False
    assert (perms.max_tool_calls, perms.max_seconds, perms.max_llm_calls) == (8, 120, 6)


async def test_denied_tool_is_not_executed_and_is_audited() -> None:
    gw, ran = _gateway(), []

    async def body() -> dict[str, Any]:
        ran.append(1)
        return {}

    for name in ("repo_write", "deploy_run", "repo_search"):  # ハニーポットと許可外
        out = await guarded(gw, name, {}, body)
        assert out["error"] == "denied"
    assert ran == []
    assert [e["decision"] for e in gw.audit.entries] == ["denied"] * 3  # type: ignore[attr-defined]
    assert gw.audit.entries[0]["injection_suspected"] is True  # type: ignore[attr-defined]
    assert gw.audit.entries[2]["injection_suspected"] is False  # type: ignore[attr-defined]


async def test_budget_exhausted_denies_without_running() -> None:
    gw, ran = _gateway(), []

    async def body() -> dict[str, Any]:
        ran.append(1)
        return {}

    for _ in range(gw.permissions.max_tool_calls):
        await guarded(gw, "run_findings", {}, body)
    out = await guarded(gw, "run_findings", {}, body)
    assert out["error"] == "denied"
    assert len(ran) == gw.permissions.max_tool_calls


async def test_allowed_tool_runs_once_and_charges_budget() -> None:
    gw = _gateway()
    out = await guarded(gw, "run_findings", {"run_id": "x"}, lambda: _async({"ok": 1}))
    assert out == {"ok": 1}
    assert gw.budget.snapshot()["tool_calls"] == 1
    assert gw.audit.entries[-1]["decision"] == "allowed"  # type: ignore[attr-defined]


async def test_long_result_is_truncated_only_for_llm() -> None:
    big = {"text": "x" * (MAX_RESULT_CHARS + 10)}
    gw = _gateway()
    cut = await guarded(gw, "spec_extract", {}, lambda: _async(big))
    assert cut["truncated"] is True and len(cut["preview"]) == MAX_RESULT_CHARS
    full = await guarded(gw, "spec_extract", {}, lambda: _async(big), truncate=False)
    assert full == big


async def test_exception_in_tool_is_audited_then_raised() -> None:
    gw = _gateway()

    async def boom() -> dict[str, Any]:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await guarded(gw, "run_findings", {}, boom)
    last = gw.audit.entries[-1]  # type: ignore[attr-defined]
    assert last["decision"] == "allowed"
    assert gw.budget.snapshot()["tool_calls"] == 1


async def test_cancelled_tool_is_audited_then_raised() -> None:
    gw = _gateway()

    async def cancelled() -> dict[str, Any]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await guarded(gw, "run_findings", {}, cancelled)
    assert [e["decision"] for e in gw.audit.entries] == ["allowed"]  # type: ignore[attr-defined]


async def test_run_findings_of_other_workspace_is_not_found() -> None:
    tools = ConsultTools(
        _deps(run_team="e2e-ws-a"), TokenIdentity(team="e2e-ws-b", token_id=uuid4()), _gateway()
    )
    assert await tools.run_findings(str(RUN_ID)) == {
        "error": "run_not_found",
        "run_id": str(RUN_ID),
    }


async def test_run_findings_of_own_workspace_returns_full_view() -> None:
    gw = _gateway()
    tools = ConsultTools(
        _deps(run_team="e2e-ws-a"), TokenIdentity(team="e2e-ws-a", token_id=uuid4()), gw
    )
    out = await tools.run_findings(str(RUN_ID))
    assert out["findings"] == [{"id": "f1"}]
    assert gw.audit.entries[-1]["decision"] == "allowed"  # type: ignore[attr-defined]


async def test_run_findings_with_malformed_id_is_not_found() -> None:
    tools = ConsultTools(
        _deps(run_team="e2e-ws-a"), TokenIdentity(team="e2e-ws-a", token_id=uuid4()), _gateway()
    )
    assert (await tools.run_findings("not-a-uuid"))["error"] == "run_not_found"


def test_only_two_functions_are_exposed_to_the_agent() -> None:
    tools = ConsultTools(_deps(), TokenIdentity(team="t", token_id=uuid4()), _gateway())
    assert [f.__name__ for f in tools.functions()] == ["spec_context", "run_findings"]


@pytest.fixture
def project_lookup(monkeypatch: pytest.MonkeyPatch) -> Callable[[str | None, str], None]:
    """ProjectRepository.get_by_repo を差し替える（team=None は未登録）。"""

    def _set(team: str | None, repo: str) -> None:
        project = None if team is None else MagicMock(id=uuid4(), team=team)
        repo_cls = MagicMock()
        repo_cls.return_value.get_by_repo = AsyncMock(return_value=project)
        monkeypatch.setattr(consultant.tools, "ProjectRepository", repo_cls)

    return _set


def _session_factory() -> MagicMock:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


async def test_spec_context_returns_context_for_own_workspace(
    project_lookup: Callable[[str | None, str], None],
) -> None:
    project_lookup("ws", "o/r")
    source = MagicMock()
    source.get = AsyncMock(return_value={"vocab": 1})
    deps = _deps(context_source=source)
    deps.session_factory = _session_factory()
    tools = ConsultTools(deps, TokenIdentity(team="ws", token_id=uuid4()), _gateway())
    assert await tools.spec_context("o/r") == {"vocab": 1}
    assert source.get.await_args.args[1:] == ("ws", "HEAD")


async def test_spec_context_of_other_workspace_or_unregistered_repo(
    project_lookup: Callable[[str | None, str], None],
) -> None:
    deps = _deps()
    deps.session_factory = _session_factory()
    tools = ConsultTools(deps, TokenIdentity(team="ws", token_id=uuid4()), _gateway())
    for team in ("other", None):
        project_lookup(team, "o/r")
        assert await tools.spec_context("o/r") == {"error": "repo_not_registered"}


async def test_spec_context_unavailable_is_returned_as_error(
    project_lookup: Callable[[str | None, str], None],
) -> None:
    project_lookup("ws", "o/r")
    source = MagicMock()
    source.get = AsyncMock(side_effect=ContextUnavailable("登録時と違うリポジトリ"))
    deps = _deps(context_source=source)
    deps.session_factory = _session_factory()
    tools = ConsultTools(deps, TokenIdentity(team="ws", token_id=uuid4()), _gateway())
    assert await tools.spec_context("o/r") == {"error": "登録時と違うリポジトリ"}
