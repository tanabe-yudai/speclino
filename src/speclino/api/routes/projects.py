"""プロジェクト画面（S45）の API。組み立ては `services/project_overview.py`。

メンバーだけが見られる。それ以外（未所属・他のワークスペースの project）は存在しない場合と同じ 404。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser
from speclino.services import project_overview
from speclino.services.workspaces import WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}/projects", tags=["projects"], responses=error_responses(401)
)


def _not_found(e: WorkspaceNotFound) -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


@router.get("", responses=error_responses(404))
async def list_projects(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            rows = await project_overview.list_projects(s, state.blobs, slug, user.uid)
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    return [r.model_dump(mode="json") for r in rows]


@router.get("/{project_id}/overview", responses=error_responses(404))
async def get_overview(
    slug: str,
    project_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            overview = await project_overview.load_overview(
                s, state.blobs, slug, user.uid, project_id
            )
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    return overview.model_dump(mode="json")
