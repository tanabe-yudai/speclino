"""可観測性の API（S71、design/2026-10-05-team-knowledge-registry.md 9 章）。集計は services/。

メンバー以外・存在しないワークスペースは 404（存在を漏らさない）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser
from speclino.services.observability import workspace_health
from speclino.services.workspaces import WorkspaceNotFound, require_member

router = APIRouter(
    prefix="/api/workspaces/{slug}", tags=["observability"], responses=error_responses(401, 404)
)


@router.get("/observability")
async def get_observability(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            health = await workspace_health(s, slug, now=datetime.now(UTC))
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    return health.model_dump(mode="json")
