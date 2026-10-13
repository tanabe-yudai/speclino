"""ワークスペースへの招待（S56）。

メールを送る仕組みは持たない。owner が発行した招待リンク（平文トークン入り）を自分で相手に送る。

- 発行・一覧・取り消しは owner だけ。メンバーで owner でない人は `NotPermitted`（403、発行と
  取り消しの拒否は監査に残す）、非メンバーは `WorkspaceNotFound`（404）
- 平文は発行時に 1 回だけ返し、保存は SHA-256 だけ（MCP トークンと同じ。高エントロピーなので
  低速ハッシュは要らない）。期限は 7 日、1 回限り、取り消せる。同じメールへの未使用の招待は、
  新しい招待を出した時点で取り消す（有効なリンクを 1 本にする）
- 受諾は、ログインした人の**確認済み**メールが招待のメールと一致（大文字小文字を区別しない）
  するときだけ。メールは識別子にしない（メンバーは uid で持つ）が、招待は「そのメールの持ち主」に
  宛てたものなので、未確認のメールで受諾させると、他人のメールで登録した第三者がリンクを
  手に入れたときに入れてしまう（フェイルクローズ）
- 使えない招待（無い・期限切れ・使用済み・取り消し済み）は区別せず `InvitationNotFound`（404）
- 既にメンバーなら role は変えない（招待で昇格・降格させない。role の変更は owner の操作だけ）

呼び出し側は `async with session_factory() as s, s.begin():` で包み、拒否の監査が rollback で
消えないよう `audit_factory` を渡す。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import TeamRecord, WorkspaceInvitation, WorkspaceMember
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.workspace import (
    AcceptedInvitation,
    AuthUser,
    InvitationAcceptStatus,
    InvitationInfo,
    InvitationPreview,
    IssuedInvitation,
    WorkspaceRole,
)
from speclino.services.workspaces import (
    NotPermitted,
    WorkspaceNotFound,
    record_denial,
    role_of,
)

TOKEN_PREFIX = "slinv_"
INVITATION_TTL = timedelta(days=7)


class InvitationNotFound(LookupError):
    """招待が使えない（無い・期限切れ・使用済み・取り消し済みを区別しない）。"""


class InvitationRejected(PermissionError):
    """受諾できない。reason は email_not_verified か email_mismatch。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def _require_owner(
    session: AsyncSession,
    audit_factory: async_sessionmaker[AsyncSession] | None,
    *,
    slug: str,
    uid: str,
    action: str | None,
) -> None:
    """owner でなければ拒否する。action があれば拒否を監査に残す（読み取りは残さない）。"""
    role = await role_of(session, slug, uid)
    if role is None:
        raise WorkspaceNotFound(slug)
    if role != "owner":
        if action is not None:
            await record_denial(
                session,
                audit_factory,
                actor=uid,
                action=action,
                target=slug,
                detail={"reason": "not_an_owner", "role": role},
            )
        raise NotPermitted(f"{uid} は {slug} の owner ではない")


async def issue(
    session: AsyncSession,
    *,
    slug: str,
    email: str,
    role: WorkspaceRole,
    actor_uid: str,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> IssuedInvitation:
    await _require_owner(
        session, audit_factory, slug=slug, uid=actor_uid, action="invitation.issue"
    )
    email = normalize_email(email)
    replaced = await session.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.team == slug,
            WorkspaceInvitation.email == email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
        .returning(WorkspaceInvitation.id)
    )
    replaced_ids = [str(i) for i in replaced.scalars()]
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = WorkspaceInvitation(
        team=slug,
        email=email,
        role=role,
        token_hash=hash_token(token),
        created_by=actor_uid,
        expires_at=datetime.now(UTC) + INVITATION_TTL,
    )
    session.add(record)
    await session.flush()
    await session.refresh(record)
    await AuditRepository(session).record(
        actor_uid,
        "invitation.issue",
        slug,
        "issued",
        {
            "invitation_id": str(record.id),
            "email": email,
            "role": role,
            "replaced": replaced_ids,
        },
    )
    return IssuedInvitation(info=_info(record), token=token)


async def list_pending(session: AsyncSession, *, slug: str, actor_uid: str) -> list[InvitationInfo]:
    """未使用で取り消されていない招待（期限切れを含む。新しい順）。"""
    await _require_owner(session, None, slug=slug, uid=actor_uid, action=None)
    stmt = (
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.team == slug,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    return [_info(r) for r in (await session.execute(stmt)).scalars()]


async def revoke(
    session: AsyncSession,
    *,
    slug: str,
    invitation_id: uuid.UUID,
    actor_uid: str,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """取り消す。別ワークスペースの ID・使用済み・取り消し済みは `InvitationNotFound`。"""
    await _require_owner(
        session, audit_factory, slug=slug, uid=actor_uid, action="invitation.revoke"
    )
    result = await session.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.team == slug,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
        .returning(WorkspaceInvitation.email)
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise InvitationNotFound(str(invitation_id))
    await AuditRepository(session).record(
        actor_uid,
        "invitation.revoke",
        slug,
        "revoked",
        {"invitation_id": str(invitation_id), "email": email},
    )


async def _usable(session: AsyncSession, token: str, *, lock: bool) -> WorkspaceInvitation:
    stmt = select(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == hash_token(token),
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
        WorkspaceInvitation.expires_at > func.now(),
    )
    if lock:
        stmt = stmt.with_for_update()
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise InvitationNotFound("invitation")
    return record


async def preview(session: AsyncSession, *, token: str) -> InvitationPreview:
    """受諾前に見せる内容。リンクを持っている人には宛先のメールとワークスペースを見せてよい。"""
    record = await _usable(session, token, lock=False)
    team = await session.get(TeamRecord, record.team)
    return InvitationPreview(
        slug=record.team,
        name=team.name if team else None,
        email=record.email,
        role=record.role,
        expires_at=record.expires_at,
    )


async def accept(
    session: AsyncSession,
    *,
    token: str,
    user: AuthUser,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AcceptedInvitation:
    record = await _usable(session, token, lock=True)
    slug = record.team
    reason: str | None = None
    if not user.email or not user.email_verified:
        reason = "email_not_verified"
    elif normalize_email(user.email) != record.email:
        reason = "email_mismatch"
    if reason is not None:
        await record_denial(
            session,
            audit_factory,
            actor=user.uid,
            action="invitation.accept",
            target=slug,
            detail={"reason": reason, "invitation_id": str(record.id)},
        )
        raise InvitationRejected(reason)

    # メンバーの増減は role の変更・削除と同じくワークスペース行のロックで直列化する
    await TeamRepository(session).ensure_and_lock(slug)
    current = await role_of(session, slug, user.uid)
    role: WorkspaceRole
    status: InvitationAcceptStatus
    if current is None:
        await session.execute(
            pg_insert(WorkspaceMember).values(
                team=slug, uid=user.uid, role=record.role, email=record.email
            )
        )
        role, status = record.role, "joined"
    else:
        # role は変えない。表示用のメールだけ確認済みのもので埋める
        await session.execute(
            update(WorkspaceMember)
            .where(WorkspaceMember.team == slug, WorkspaceMember.uid == user.uid)
            .values(email=record.email)
        )
        role, status = current, "already_member"
    record.accepted_at = datetime.now(UTC)
    record.accepted_by = user.uid
    await session.flush()
    await AuditRepository(session).record(
        user.uid,
        "invitation.accept",
        slug,
        "accepted",
        {
            "invitation_id": str(record.id),
            "email": record.email,
            "role": role,
            "status": status,
        },
    )
    return AcceptedInvitation(slug=slug, role=role, status=status)


def _info(record: WorkspaceInvitation) -> InvitationInfo:
    return InvitationInfo(
        id=record.id,
        email=record.email,
        role=record.role,
        created_by=record.created_by,
        created_at=record.created_at,
        expires_at=record.expires_at,
        accepted_at=record.accepted_at,
        revoked_at=record.revoked_at,
    )


__all__ = [
    "INVITATION_TTL",
    "TOKEN_PREFIX",
    "InvitationNotFound",
    "InvitationRejected",
    "NotPermitted",
    "WorkspaceNotFound",
    "accept",
    "hash_token",
    "issue",
    "list_pending",
    "normalize_email",
    "preview",
    "revoke",
]
