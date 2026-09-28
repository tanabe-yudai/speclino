"""ログインしている利用者と所属ワークスペース（S53）。

UI は所属が無いときに「ワークスペースに招待されていない」と表示し、管理者は自分の uid を
ここで確かめて管理 CLI（`speclino workspace add-member`）に渡す。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import error_responses
from speclino.db.repositories import TeamRepository
from speclino.domain.workspace import AuthUser

router = APIRouter(prefix="/api/me", tags=["me"], responses=error_responses(401))


@router.get("")
async def get_me(
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    async with state.session_factory() as session:
        memberships = await TeamRepository(session).memberships(user.uid)
    return {
        "uid": user.uid,
        "email": user.email,
        "email_verified": user.email_verified,
        "workspaces": [
            {"slug": slug, "name": name, "role": role} for slug, name, role in memberships
        ],
    }
