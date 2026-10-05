"""知識の配信 API（S68、design/2026-10-05-team-knowledge-registry.md 7.1）。判断は services/。

- `/api/workspaces/{slug}/sync-tokens`: sync トークンの発行・失効（owner / approver）と一覧
  （メンバー）。メンバーでなければ 404、role が足りなければ 403
- `GET /api/sync/bundle?repo_id=`: sync トークン（`Authorization: Bearer slsync_...`）で、
  そのリポジトリに当たる最新の bundle の書き出し内容と署名つきの lock を返す。トークンが無効なら
  401、トークンのリポジトリでなければ 404（他のリポジトリの存在を漏らさない）
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.knowledge_sync import SyncTokenInfo
from speclino.domain.workspace import AuthUser
from speclino.services import knowledge_sync, sync_tokens
from speclino.services.workspaces import NotPermitted, WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}/sync-tokens",
    tags=["sync-tokens"],
    responses=error_responses(401, 404),
)
bundle_router = APIRouter(prefix="/api/sync", tags=["sync"], responses=error_responses(401, 404))


class IssueSyncTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: uuid.UUID


def _info_view(info: SyncTokenInfo) -> dict[str, Any]:
    return {
        "id": str(info.id),
        "project_id": str(info.project_id),
        "repo_full_name": info.repo_full_name,
        "name": info.name,
        "created_by": info.created_by,
        "created_at": info.created_at.isoformat(),
        "last_used_at": info.last_used_at.isoformat() if info.last_used_at else None,
        "revoked_at": info.revoked_at.isoformat() if info.revoked_at else None,
    }


def _http(e: Exception) -> Exception:
    if isinstance(e, NotPermitted):
        return api_error(
            403, "not_an_approver", "sync トークンを管理できるのは owner / approver だけ"
        )
    return api_error(404, "workspace_not_found", "ワークスペースかプロジェクトが無い")


@router.post("", status_code=201, responses=error_responses(403))
async def issue_sync_token(
    slug: str,
    body: IssueSyncTokenRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            issued = await sync_tokens.issue(
                s, slug=slug, project_id=body.project_id, name=body.name, actor_uid=user.uid
            )
    except (WorkspaceNotFound, NotPermitted) as e:
        raise _http(e) from e
    # 平文はこの応答（発行直後）だけに載せる
    return {**_info_view(issued.info), "token": issued.token}


@router.get("")
async def list_sync_tokens(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            tokens = await sync_tokens.list_tokens(s, slug=slug, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise _http(e) from e
    return [_info_view(t) for t in tokens]


@router.delete("/{token_id}", status_code=204, responses=error_responses(403))
async def revoke_sync_token(
    slug: str,
    token_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> None:
    try:
        async with state.session_factory() as s, s.begin():
            await sync_tokens.revoke(s, slug=slug, token_id=token_id, actor_uid=user.uid)
    except (WorkspaceNotFound, NotPermitted) as e:
        raise _http(e) from e


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""


@bundle_router.get("/bundle", responses=error_responses(409))
async def get_sync_bundle(
    request: Request,
    repo_id: int = Query(description="GitHub のリポジトリ ID"),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    token = _bearer(request)
    async with state.session_factory() as s, s.begin():
        identity = await sync_tokens.authenticate(s, token) if token else None
    if identity is None:
        raise api_error(401, "unauthenticated", "sync トークンが必要")
    try:
        async with state.session_factory() as s, s.begin():
            payload = await knowledge_sync.fetch_for_sync(s, identity, repo_id)
    except LookupError as e:
        raise api_error(404, "repository_not_found", "このトークンのリポジトリではない") from e
    except ValueError as e:
        raise api_error(409, "bundle_unavailable", f"知識を配信できない: {e}") from e
    return payload.model_dump(mode="json")


__all__ = ["bundle_router", "router"]
