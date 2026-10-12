"""メンバーと招待の API（S56、design/2026-09-27-workspaces.md）。

判断は `services/members.py` と `services/invitations.py` に集約し、ここは HTTP の形にするだけ。
非メンバーは 404（存在を漏らさない）、owner の操作を owner 以外のメンバーが呼ぶと 403。
招待の受諾は `/api/invitations`（受諾する人はまだメンバーではないので slug を URL に取らない）。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import (
    AuthUser,
    InvitationInfo,
    IssuedInvitation,
    WorkspaceMemberView,
    WorkspaceRole,
)
from speclino.services import invitations as invitation_service
from speclino.services import members as member_service
from speclino.services.workspaces import NotPermitted, WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}", tags=["members"], responses=error_responses(401)
)
accept_router = APIRouter(
    prefix="/api/invitations", tags=["members"], responses=error_responses(401)
)

# 形だけの確認（宛先の実在は確かめようがない）。正規化（前後の空白・小文字化）はサービス側
_EMAIL_PATTERN = r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$"


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=_EMAIL_PATTERN)
    role: WorkspaceRole = "member"


class RoleRequest(BaseModel):
    role: WorkspaceRole


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


def _not_found() -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


def _not_owner() -> Exception:
    return api_error(403, "not_an_owner", "この操作は owner だけができる")


def _member_view(m: WorkspaceMemberView) -> dict[str, Any]:
    return {
        "uid": m.uid,
        "display_name": m.display_name,
        "email": m.email,
        "email_verified": m.email_verified,
        "label": m.label,
        "role": m.role,
        "joined_at": m.joined_at.isoformat(),
    }


def _invitation_view(i: InvitationInfo) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "email": i.email,
        "role": i.role,
        "created_by": i.created_by,
        "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat(),
        "accepted_at": i.accepted_at.isoformat() if i.accepted_at else None,
        "revoked_at": i.revoked_at.isoformat() if i.revoked_at else None,
    }


def _issued_view(issued: IssuedInvitation) -> dict[str, Any]:
    # 平文はこの応答（発行直後）だけに載せる。一覧・以後のどの応答にも出さない
    return {**_invitation_view(issued.info), "token": issued.token}


# ------------------------------------------------------------------ メンバー


@router.get("/members", responses=error_responses(404))
async def list_members(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            rows = await member_service.list_members(s, slug=slug, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return [_member_view(m) for m in rows]


@router.patch("/members/{uid}", responses=error_responses(403, 404, 409))
async def change_role(
    slug: str,
    uid: str,
    body: RoleRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            member = await member_service.change_role(
                s,
                slug=slug,
                actor_uid=user.uid,
                target_uid=uid,
                role=body.role,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except member_service.MemberNotFound as e:
        raise api_error(404, "member_not_found", "メンバーが無い") from e
    except NotPermitted as e:
        raise _not_owner() from e
    except member_service.LastOwner as e:
        raise api_error(409, "last_owner", "最後の owner は降格できない") from e
    return _member_view(member)


@router.delete("/members/{uid}", status_code=204, responses=error_responses(403, 404, 409))
async def remove_member(
    slug: str,
    uid: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> None:
    try:
        async with state.session_factory() as s, s.begin():
            await member_service.remove_member(
                s,
                slug=slug,
                actor_uid=user.uid,
                target_uid=uid,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except member_service.MemberNotFound as e:
        raise api_error(404, "member_not_found", "メンバーが無い") from e
    except NotPermitted as e:
        raise _not_owner() from e
    except member_service.LastOwner as e:
        raise api_error(409, "last_owner", "最後の owner は外せない") from e


# ------------------------------------------------------------------ 招待（owner）


@router.post("/invitations", status_code=201, responses=error_responses(403, 404))
async def issue_invitation(
    slug: str,
    body: InviteRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            issued = await invitation_service.issue(
                s,
                slug=slug,
                email=body.email,
                role=body.role,
                actor_uid=user.uid,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except NotPermitted as e:
        raise _not_owner() from e
    return _issued_view(issued)


@router.get("/invitations", responses=error_responses(403, 404))
async def list_invitations(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            rows = await invitation_service.list_pending(s, slug=slug, actor_uid=user.uid)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except NotPermitted as e:
        raise _not_owner() from e
    return [_invitation_view(i) for i in rows]


@router.delete("/invitations/{invitation_id}", status_code=204, responses=error_responses(403, 404))
async def revoke_invitation(
    slug: str,
    invitation_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> None:
    try:
        async with state.session_factory() as s, s.begin():
            await invitation_service.revoke(
                s,
                slug=slug,
                invitation_id=invitation_id,
                actor_uid=user.uid,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except invitation_service.InvitationNotFound as e:
        raise api_error(404, "invitation_not_found", "招待が無い") from e
    except NotPermitted as e:
        raise _not_owner() from e


# ------------------------------------------------------------------ 招待（受け取った人）

_INVALID_INVITATION = "招待が無効です（期限切れ・使用済み・取り消し済みを含む）"


@accept_router.post("/preview", responses=error_responses(404))
async def preview_invitation(
    body: TokenRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    """受諾前の確認。トークンは URL に載せない（POST の本文で送る）。"""
    try:
        async with state.session_factory() as s:
            p = await invitation_service.preview(s, token=body.token)
    except invitation_service.InvitationNotFound as e:
        raise api_error(404, "invitation_not_found", _INVALID_INVITATION) from e
    return {
        "slug": p.slug,
        "name": p.name,
        "email": p.email,
        "role": p.role,
        "expires_at": p.expires_at.isoformat(),
    }


@accept_router.post("/accept", responses=error_responses(403, 404))
async def accept_invitation(
    body: TokenRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            accepted = await invitation_service.accept(
                s, token=body.token, user=user, audit_factory=state.session_factory
            )
    except invitation_service.InvitationNotFound as e:
        raise api_error(404, "invitation_not_found", _INVALID_INVITATION) from e
    except invitation_service.InvitationRejected as e:
        if e.reason == "email_not_verified":
            raise api_error(
                403, "email_not_verified", "メールアドレスが確認されていないため受諾できない"
            ) from e
        raise api_error(
            403,
            "invitation_email_mismatch",
            "招待されたメールアドレスのアカウントでログインして受諾する",
        ) from e
    return {"slug": accepted.slug, "role": accepted.role, "status": accepted.status}


__all__ = ["accept_router", "router"]
