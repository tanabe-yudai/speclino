"""`speclino sync` 専用のトークン（S68）。

- 読み取り専用で、1 つのワークスペースの 1 つのプロジェクト（リポジトリ）に限る
- 発行・失効はワークスペースの owner / approver。一覧はメンバー全員（平文もハッシュも出さない）。
  メンバーでなければ `WorkspaceNotFound`（404）、role が足りなければ `NotPermitted`（403）
- 平文は発行時の応答で 1 回だけ返し、保存は SHA-256 だけ（`mcp_tokens` と同じ作り）。照合は
  ハッシュの一致で引く（平文を比較しないので比較の時間から平文は漏れない）
- トークンの平文はログ・監査・lock に出さない（監査はトークン ID だけ）

呼び出し側は `async with session_factory() as s, s.begin():` で包む。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, SyncTokenRecord
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.sync_tokens import SyncTokenRepository
from speclino.domain.knowledge_sync import IssuedSyncToken, SyncIdentity, SyncTokenInfo
from speclino.domain.workspace import APPROVER_ROLES
from speclino.services.workspaces import NotPermitted, WorkspaceNotFound, role_of

TOKEN_PREFIX = "slsync_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _require_approver(session: AsyncSession, slug: str, uid: str) -> None:
    role = await role_of(session, slug, uid)
    if role is None:
        raise WorkspaceNotFound(slug)
    if role not in APPROVER_ROLES:
        raise NotPermitted(f"{uid} は {slug} の sync トークンを管理できない")


async def _workspace_project(session: AsyncSession, slug: str, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.team != slug:
        raise WorkspaceNotFound(slug)
    return project


async def issue(
    session: AsyncSession, *, slug: str, project_id: uuid.UUID, name: str, actor_uid: str
) -> IssuedSyncToken:
    """project（slug に属するもの）に限ったトークンを発行する。別のワークスペースのものは 404。"""
    await _require_approver(session, slug, actor_uid)
    project = await _workspace_project(session, slug, project_id)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = await SyncTokenRepository(session).create(
        team=slug,
        project_id=project.id,
        name=name,
        token_hash=hash_token(token),
        created_by=actor_uid,
    )
    await AuditRepository(session).record(
        actor_uid,
        "sync_token.issue",
        slug,
        "issued",
        {"token_id": str(record.id), "name": name, "project_id": str(project.id)},
    )
    return IssuedSyncToken(info=_info(record, project.repo_full_name), token=token)


async def list_tokens(session: AsyncSession, *, slug: str, actor_uid: str) -> list[SyncTokenInfo]:
    if await role_of(session, slug, actor_uid) is None:
        raise WorkspaceNotFound(slug)
    return [_info(r, repo) for r, repo in await SyncTokenRepository(session).list_for_team(slug)]


async def revoke(session: AsyncSession, *, slug: str, token_id: uuid.UUID, actor_uid: str) -> None:
    """失効させる。別のワークスペースのトークン・失効済みも `WorkspaceNotFound`（404）。"""
    await _require_approver(session, slug, actor_uid)
    if not await SyncTokenRepository(session).revoke(token_id=token_id, team=slug):
        raise WorkspaceNotFound(slug)
    await AuditRepository(session).record(
        actor_uid, "sync_token.revoke", slug, "revoked", {"token_id": str(token_id)}
    )


async def authenticate(session: AsyncSession, token: str) -> SyncIdentity | None:
    """有効なトークンの身元。無い・失効済み・プロジェクトが別のワークスペースに移ったなら None。"""
    if not token.startswith(TOKEN_PREFIX):
        return None
    repo = SyncTokenRepository(session)
    record = await repo.find_active(hash_token(token))
    if record is None:
        return None
    project = await session.get(Project, record.project_id)
    if project is None or project.team != record.team:
        return None
    await repo.touch(record.id)
    return SyncIdentity(token_id=record.id, workspace=record.team, project_id=record.project_id)


def _info(record: SyncTokenRecord, repo_full_name: str) -> SyncTokenInfo:
    return SyncTokenInfo(
        id=record.id,
        project_id=record.project_id,
        repo_full_name=repo_full_name,
        name=record.name,
        created_by=record.created_by,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
    )


__all__ = ["TOKEN_PREFIX", "authenticate", "hash_token", "issue", "list_tokens", "revoke"]
