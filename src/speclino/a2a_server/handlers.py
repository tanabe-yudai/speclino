"""A2A のスキル（S59 の 3 つと S67 の spec_suggest）を app の state から組み立てる（S59）。

費用ガードが無い構成では組み立てない（フェイルクローズ）。計量なしで Gemini を呼ばせない。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from google.adk.models.base_llm import BaseLlm

from speclino.a2a_server.executor import SkillCall, SkillHandler, SkillsUnavailable
from speclino.adapters.llm.gemini_json import GeminiJsonLlm
from speclino.agent.consultant.requirement_consult import run_requirement_consult
from speclino.agent.consultant.run_clarification import run_run_clarification
from speclino.agent.consultant.spec_review import run_spec_review
from speclino.agent.consultant.spec_suggest import run_spec_suggest
from speclino.agent.consultant.tools import ConsultBudget, ConsultDeps
from speclino.api.deps import AppState
from speclino.domain.a2a import SkillId, TokenIdentity
from speclino.ports.text_llm import TextLlm
from speclino.services import agent_supplements
from speclino.services.mcp_context import InProcessContextSource
from speclino.services.run_view import run_view


def build_deps(
    state: AppState,
    *,
    spec_llm: TextLlm | None = None,
    agent_model: str | BaseLlm | None = None,
) -> ConsultDeps:
    """state から議論役の依存を作る。費用ガードが無ければ SkillsUnavailable。"""
    cost_guard = state.service.cost_guard
    if cost_guard is None:
        raise SkillsUnavailable("費用ガードが設定されていないため、A2A のスキルを使えません")
    settings = state.settings
    model_name = settings.a2a_model or settings.agent_model
    factory = state.session_factory
    context_source = state.context_source or InProcessContextSource(
        factory, state.code_host, state.service.config.resolve_team_policy
    )

    async def load_run_view(rid: uuid.UUID, workspace: str) -> dict[str, Any] | None:
        return await run_view(factory, rid, workspace, full=True)

    async def record_supplement(
        *,
        identity: TokenIdentity,
        run_id: str,
        requirement_id: str | None,
        question: str,
        content: str,
        a2a_task_id: str,
    ) -> None:
        """新しいセッションで補足を記録する。他のワークスペースの run は RunNotInWorkspace。"""
        async with factory() as session, session.begin():
            await agent_supplements.record(
                session,
                identity=identity,
                run_id=uuid.UUID(run_id),
                requirement_id=requirement_id,
                question=question,
                content=content,
                a2a_task_id=a2a_task_id,
            )

    return ConsultDeps(
        session_factory=factory,
        context_source=context_source,
        service=state.service,
        cost_guard=cost_guard,
        spec_llm=spec_llm or GeminiJsonLlm(model_name),
        agent_model=agent_model or model_name,
        today=lambda: datetime.now(UTC).date(),
        load_run_view=load_run_view,
        record_supplement=record_supplement,
        budget=ConsultBudget(factory, cost_guard),
    )


def handlers_for(deps: ConsultDeps) -> dict[SkillId, SkillHandler]:
    async def spec_review(call: SkillCall) -> None:
        await run_spec_review(call, deps)

    async def requirement_consult(call: SkillCall) -> None:
        await run_requirement_consult(call, deps)

    async def run_clarification(call: SkillCall) -> None:
        await run_run_clarification(call, deps)

    async def spec_suggest(call: SkillCall) -> None:
        await run_spec_suggest(call, deps)

    return {
        "spec_review": spec_review,
        "requirement_consult": requirement_consult,
        "run_clarification": run_clarification,
        "spec_suggest": spec_suggest,
    }


def build_handlers(
    state: AppState,
    *,
    spec_llm: TextLlm | None = None,
    agent_model: str | BaseLlm | None = None,
) -> dict[SkillId, SkillHandler]:
    """本物のスキル。テストは spec_llm / agent_model に台本の LLM を渡す。"""
    return handlers_for(build_deps(state, spec_llm=spec_llm, agent_model=agent_model))
