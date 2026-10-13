"""AI の書き換え提案の API（S67）。判断は services/spec_suggest.py。

メンバーだけが使える（別のワークスペース・存在しないワークスペースは 404）。保存も適用もしない。
返すのは本文の置換の提案と、当てた後の検証の要約だけ（IR は返さない）。
body は preview と同じく 512KB でストリームを打ち切る。費用ガードが無い構成では LLM を呼ばない
（503、フェイルクローズ）。`run_suggest` は MCP の `spec_suggest` も使う配線。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from speclino.adapters.llm.gemini_json import GeminiJsonLlm
from speclino.agent.consultant.tools import ConsultBudget
from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.api.routes.spec_preview import PreviewRequest, _read_limited
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import SpecSuggestions
from speclino.domain.spec_ir import SpecIR
from speclino.domain.workspace import AuthUser
from speclino.ports.text_llm import TextLlm
from speclino.services.spec_suggest import (
    SuggestBudgetExceeded,
    SuggestFailed,
    SuggestInputTooLarge,
    SuggestRefused,
    suggest,
)
from speclino.services.workspaces import WorkspaceNotFound, require_member
from speclino.spec.uml import compile_spec

router = APIRouter(
    prefix="/api/workspaces/{slug}/specs", tags=["knowledge"], responses=error_responses(401, 404)
)


class SuggestUnavailable(SuggestRefused):
    """費用ガードが無い構成。計量なしで LLM を呼ばない。"""


def _llm(state: AppState) -> TextLlm:
    """提案の LLM。Gemini のクライアントは初回に作り、state に置いて使い回す。"""
    if state.spec_suggest_llm is None:
        settings = state.settings
        state.spec_suggest_llm = GeminiJsonLlm(settings.a2a_model or settings.agent_model)
    return state.spec_suggest_llm


async def run_suggest(
    state: AppState,
    markdown: str,
    *,
    domain: DomainSpec | None = None,
    legacy_ir: list[SpecIR] | None = None,
) -> SpecSuggestions:
    """今の本文を `compile_spec` で診断し、提案を作る。断るときは `SuggestRefused` の派生。"""
    cost_guard = state.service.cost_guard
    if cost_guard is None:
        raise SuggestUnavailable("費用ガードが設定されていないため、AI の提案を使えません")
    legacy = list(legacy_ir or [])
    parsed = await asyncio.to_thread(compile_spec, markdown, domain=domain, legacy_ir=legacy)
    diagnostics = parsed.diagnostics
    suggestions = await suggest(
        markdown,
        diagnostics,
        llm=_llm(state),
        budget=ConsultBudget(state.session_factory, cost_guard),
        cost_guard=cost_guard,
        today=datetime.now(UTC).date(),
        domain=domain,
        legacy_ir=legacy,
    )
    errors_before = sum(1 for d in diagnostics if d.severity == "error")
    return SpecSuggestions(errors_before=errors_before, suggestions=suggestions)


@router.post(
    "/suggest",
    responses=error_responses(413, 422, 429, 502, 503),
    # body は 512KB の上限を確かめてから自前で読む（preview と同じ形）
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
async def suggest_spec(
    slug: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    raw = await _read_limited(request)
    try:
        body = PreviewRequest.model_validate_json(raw)
    except ValidationError as e:
        errors = e.errors(include_input=False, include_url=False)
        raise api_error(422, "validation_error", str(errors)) from e
    try:
        result = await run_suggest(
            state, body.markdown, domain=body.domain, legacy_ir=body.legacy_ir
        )
    except SuggestInputTooLarge as e:
        raise api_error(413, "spec_too_large", str(e)) from e
    except SuggestBudgetExceeded as e:
        raise api_error(429, "cost_limit", str(e)) from e
    except SuggestUnavailable as e:
        raise api_error(503, "suggest_unavailable", str(e)) from e
    except SuggestFailed as e:
        raise api_error(502, "suggest_failed", str(e)) from e
    return result.model_dump(mode="json")
