"""worker ロールの内部エンドポイント: MCP `context_get` の組み立て（S54）。

秘密鍵を持つ worker でスナップショットを取るため、web から OIDC で呼ばれる。呼び出せるのは
Cloud Run の IAM で許可した SA（tasks と web）だけ。web は、トークンのワークスペースに登録されて
いることを確かめた project_id だけを渡す。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error
from speclino.domain.run import GitRef
from speclino.services.mcp_context import ContextUnavailable, InProcessContextSource

router = APIRouter(prefix="/internal/mcp", tags=["internal"])


class ContextRequest(BaseModel):
    project_id: uuid.UUID
    # web が確かめたトークンのワークスペース。worker でも project の登録先と照合する
    workspace: str = Field(min_length=1)
    ref: GitRef = "HEAD"


@router.post("/context")
async def mcp_context(body: ContextRequest, state: AppState = Depends(get_state)) -> dict[str, Any]:
    source = InProcessContextSource(
        state.session_factory, state.code_host, state.service.config.resolve_team_policy
    )
    try:
        return await source.get(body.project_id, body.workspace, body.ref)
    except ContextUnavailable as e:
        raise api_error(409, "context_unavailable", str(e)) from e
