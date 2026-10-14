"""ワークスペースを自分で作る API。判断は `services/workspace_create.py` に集約する。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser
from speclino.services import workspace_create as service

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"], responses=error_responses(401))


class CreateWorkspaceRequest(BaseModel):
    # 形の検証はサービス側（API と CLI で同じ規則にする）
    slug: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)


@router.post("", status_code=201, responses=error_responses(409, 422))
async def create_workspace(
    body: CreateWorkspaceRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    """ログインしている人を owner とするワークスペースを作る。応答は `/api/me` の所属と同じ形。"""
    try:
        async with state.session_factory() as session, session.begin():
            name = await service.create_own_workspace(
                session, uid=user.uid, slug=body.slug, name=body.name
            )
    except service.InvalidWorkspace as e:
        raise api_error(422, "invalid_workspace", str(e)) from e
    except service.WorkspaceExists as e:
        raise api_error(
            409,
            "workspace_exists",
            "この識別子のワークスペースは既にあります。別の識別子にしてください",
        ) from e
    except service.WorkspaceLimitReached as e:
        raise api_error(
            409,
            "workspace_limit",
            f"オーナーのワークスペースは {service.MAX_OWNED_WORKSPACES} 個までです",
        ) from e
    return {"slug": body.slug, "name": name, "role": "owner"}
