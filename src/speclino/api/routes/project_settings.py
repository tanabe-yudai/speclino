"""project の設定とオンボーディングの API（S47）。判断は `services/project_settings.py`。

メンバーなら読める。設定の変更は owner / approver だけ（それ以外は 403、拒否は監査に残る）。
PR の承認者（`approvers`）の変更は owner だけ（approver が変えようとすると 403）。
非メンバー・別ワークスペースの project は 404。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.project_settings import ProjectSettings
from speclino.domain.workspace import AuthUser
from speclino.services import project_settings as service
from speclino.services.workspaces import NotPermitted, WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}", tags=["project-settings"], responses=error_responses(401)
)


def _not_found() -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


@router.get("/onboarding", responses=error_responses(404))
async def get_onboarding(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            onboarding = await service.workspace_onboarding(s, slug=slug, uid=user.uid)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return onboarding.model_dump(mode="json")


@router.get("/projects/{project_id}/settings", responses=error_responses(404))
async def get_settings(
    slug: str,
    project_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            view = await service.get_settings(s, slug=slug, uid=user.uid, project_id=project_id)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return view.model_dump(mode="json")


@router.put("/projects/{project_id}/settings", responses=error_responses(403, 404))
async def update_settings(
    slug: str,
    project_id: uuid.UUID,
    body: ProjectSettings,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            view = await service.update_settings(
                s,
                slug=slug,
                uid=user.uid,
                project_id=project_id,
                settings=body,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except service.ApproversOwnerOnly as e:
        raise api_error(
            403, "approvers_owner_only", "PR の承認者を変更できるのは owner だけ"
        ) from e
    except NotPermitted as e:
        raise api_error(403, "not_an_approver", "設定を変更できるのは owner / approver だけ") from e
    return view.model_dump(mode="json")


__all__ = ["router"]
