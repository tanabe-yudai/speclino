"""ワークスペースのメンバーと role（S56、design/2026-09-27-workspaces.md）。

- 一覧はメンバーなら誰でも見られる（非メンバーは `WorkspaceNotFound` → 404）
- role の変更とメンバーの削除は owner だけ。owner でないメンバーは `NotPermitted`（403）で拒否し、
  拒否も監査に残す。これで S42 の「改訂の作者が自分を承認者に足せない」を保つ
- 最後の owner は降格も削除もできない（`LastOwner` → 409。誰も管理できないワークスペースを
  作らない）。
  判定と書き込みはワークスペース行のロック（`TeamRepository.ensure_and_lock`）の中で行い、2 人の
  owner が同時に互いを降格して owner が 0 人になる競合を防ぐ
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import WorkspaceMember
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.workspace import WorkspaceMemberView, WorkspaceRole
from speclino.services.workspaces import (
    NotPermitted,
    WorkspaceNotFound,
    record_denial,
    role_of,
)


class MemberNotFound(LookupError):
    """対象の uid がそのワークスペースのメンバーではない。"""


class LastOwner(ValueError):
    """最後の owner を降格・削除しようとした。"""


async def list_members(
    session: AsyncSession, *, slug: str, actor_uid: str
) -> list[WorkspaceMemberView]:
    if await role_of(session, slug, actor_uid) is None:
        raise WorkspaceNotFound(slug)
    stmt = (
        select(WorkspaceMember)
        .where(WorkspaceMember.team == slug)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.uid)
    )
    return [
        WorkspaceMemberView(uid=m.uid, email=m.email, role=m.role, joined_at=m.created_at)
        for m in (await session.execute(stmt)).scalars()
    ]


async def _owner_change_preamble(
    session: AsyncSession,
    audit_factory: async_sessionmaker[AsyncSession] | None,
    *,
    slug: str,
    actor_uid: str,
    target_uid: str,
    action: str,
    detail: dict[str, str],
) -> WorkspaceRole:
    """呼び出した人が owner であることを確かめ、ロックを取り、対象の今の role を返す。"""
    if await role_of(session, slug, actor_uid) is None:
        raise WorkspaceNotFound(slug)
    # メンバーであること（＝ワークスペース行があること）を確かめてからロックする。ロックの後に
    # 呼び出した人の role を読み直し、同時に降格された owner の操作を通さない
    await TeamRepository(session).ensure_and_lock(slug)
    actor_role = await role_of(session, slug, actor_uid)
    if actor_role != "owner":
        await record_denial(
            session,
            audit_factory,
            actor=actor_uid,
            action=action,
            target=slug,
            detail={"reason": "not_an_owner", "uid": target_uid, **detail},
        )
        raise NotPermitted(f"{actor_uid} は {slug} の owner ではない")
    current = await role_of(session, slug, target_uid)
    if current is None:
        raise MemberNotFound(target_uid)
    return current


async def _ensure_not_last_owner(
    session: AsyncSession,
    audit_factory: async_sessionmaker[AsyncSession] | None,
    *,
    slug: str,
    actor_uid: str,
    target_uid: str,
    action: str,
    detail: dict[str, str],
) -> None:
    owners = (
        await session.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.team == slug, WorkspaceMember.role == "owner")
        )
    ).scalar_one()
    if owners <= 1:
        await record_denial(
            session,
            audit_factory,
            actor=actor_uid,
            action=action,
            target=slug,
            detail={"reason": "last_owner", "uid": target_uid, **detail},
        )
        raise LastOwner(f"{slug} の最後の owner は外せない")


async def change_role(
    session: AsyncSession,
    *,
    slug: str,
    actor_uid: str,
    target_uid: str,
    role: WorkspaceRole,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> WorkspaceMemberView:
    action = "member.role_change"
    current = await _owner_change_preamble(
        session,
        audit_factory,
        slug=slug,
        actor_uid=actor_uid,
        target_uid=target_uid,
        action=action,
        detail={"after": role},
    )
    if current == "owner" and role != "owner":
        await _ensure_not_last_owner(
            session,
            audit_factory,
            slug=slug,
            actor_uid=actor_uid,
            target_uid=target_uid,
            action=action,
            detail={"before": current, "after": role},
        )
    row = (
        await session.execute(
            update(WorkspaceMember)
            .where(WorkspaceMember.team == slug, WorkspaceMember.uid == target_uid)
            .values(role=role)
            .returning(WorkspaceMember)
        )
    ).scalar_one()
    await AuditRepository(session).record(
        actor_uid,
        action,
        slug,
        "changed",
        {"uid": target_uid, "before": current, "after": role},
    )
    return WorkspaceMemberView(
        uid=row.uid, email=row.email, role=row.role, joined_at=row.created_at
    )


async def remove_member(
    session: AsyncSession,
    *,
    slug: str,
    actor_uid: str,
    target_uid: str,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    action = "member.remove"
    current = await _owner_change_preamble(
        session,
        audit_factory,
        slug=slug,
        actor_uid=actor_uid,
        target_uid=target_uid,
        action=action,
        detail={},
    )
    if current == "owner":
        await _ensure_not_last_owner(
            session,
            audit_factory,
            slug=slug,
            actor_uid=actor_uid,
            target_uid=target_uid,
            action=action,
            detail={"role": current},
        )
    await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.team == slug, WorkspaceMember.uid == target_uid
        )
    )
    await AuditRepository(session).record(
        actor_uid, action, slug, "removed", {"uid": target_uid, "role": current}
    )


__all__ = [
    "LastOwner",
    "MemberNotFound",
    "NotPermitted",
    "WorkspaceNotFound",
    "change_role",
    "list_members",
    "remove_member",
]
