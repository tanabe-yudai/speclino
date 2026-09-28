"""ワークスペース単位の MCP トークン API（S54、design/2026-09-27-workspaces.md 4・8.1 節）。

発行・一覧・失効はワークスペースの owner だけ（判断は `services/mcp_tokens.py` に集約）。
ここは HTTP の形にするだけで、`WorkspaceNotFound`（owner ではない・ワークスペースが無い・
別ワークスペースのトークン ID・失効済みの再失効、を区別しない）を 404 に変換する。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser, IssuedMcpToken, McpTokenInfo
from speclino.services import mcp_tokens as mcp_token_service
from speclino.services.mcp_tokens import WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}/mcp-tokens",
    tags=["mcp-tokens"],
    responses=error_responses(401),
)


class IssueTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _info_view(info: McpTokenInfo) -> dict[str, Any]:
    return {
        "id": str(info.id),
        "name": info.name,
        "created_by": info.created_by,
        "created_at": info.created_at.isoformat(),
        "last_used_at": info.last_used_at.isoformat() if info.last_used_at else None,
        "revoked_at": info.revoked_at.isoformat() if info.revoked_at else None,
    }


def _issued_view(issued: IssuedMcpToken) -> dict[str, Any]:
    # 平文はこの応答（発行直後）だけに載せる。一覧・以後のどの応答にも出さない
    return {**_info_view(issued.info), "token": issued.token}


@router.post("", status_code=201, responses=error_responses(404))
async def issue_token(
    slug: str,
    body: IssueTokenRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            issued = await mcp_token_service.issue(s, slug=slug, name=body.name, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    return _issued_view(issued)


@router.get("", responses=error_responses(404))
async def list_tokens(
    slug: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s, s.begin():
            tokens = await mcp_token_service.list_tokens(s, slug=slug, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    return [_info_view(t) for t in tokens]


@router.delete("/{token_id}", status_code=204, responses=error_responses(404))
async def revoke_token(
    slug: str,
    token_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> None:
    try:
        async with state.session_factory() as s, s.begin():
            await mcp_token_service.revoke(s, slug=slug, token_id=token_id, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e


__all__ = ["router"]
