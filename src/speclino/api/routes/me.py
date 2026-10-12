"""ログインしている利用者と所属ワークスペース（S53）、本人のプロフィール。

UI は所属が無いときに「ワークスペースに招待されていない」と表示し、管理者は自分の uid を
ここで確かめて管理 CLI（`speclino workspace add-member`）に渡す。`GET` のたびに ID トークンの
メールと確認状態をプロフィールに記録する（判断は `services/profiles.py`）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.repositories import TeamRepository
from speclino.domain.workspace import AuthUser, UserProfile
from speclino.services import profiles as profile_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"], responses=error_responses(401))


class ProfileRequest(BaseModel):
    # 文字数・制御文字の検証はサービス側（前後の空白を除いてから数える）
    display_name: str | None = Field(max_length=200)


def _profile_view(p: UserProfile) -> dict[str, Any]:
    return {
        "uid": p.uid,
        "display_name": p.display_name,
        "email": p.email,
        "email_verified": p.email_verified,
    }


@router.get("")
async def get_me(
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    # プロフィールの記録は表示のためだけ。失敗しても（表が無い・DB の一時的な失敗）巻き戻して
    # me は返し、画面を開けなくしない
    display_name: str | None = None
    try:
        async with state.session_factory() as session, session.begin():
            display_name = (await profile_service.record_sign_in(session, user)).display_name
    except Exception as e:  # noqa: BLE001 - 理由を問わず me の応答を優先する
        logger.warning("プロフィールを記録できない: reason=%s", type(e).__name__)
    async with state.session_factory() as session:
        memberships = await TeamRepository(session).memberships(user.uid)
    return {
        "uid": user.uid,
        "email": user.email,
        "email_verified": user.email_verified,
        "display_name": display_name,
        "workspaces": [
            {"slug": slug, "name": name, "role": role} for slug, name, role in memberships
        ],
    }


@router.patch("/profile", responses=error_responses(422))
async def update_profile(
    body: ProfileRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    """本人の表示名を変える（null で消す）。他人のプロフィールを変える API は無い。"""
    try:
        async with state.session_factory() as session, session.begin():
            profile = await profile_service.update_display_name(session, user, body.display_name)
    except profile_service.InvalidDisplayName as e:
        raise api_error(422, "invalid_display_name", str(e)) from e
    return _profile_view(profile)
