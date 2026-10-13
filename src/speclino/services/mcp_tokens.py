"""ワークスペース単位の MCP トークン（S54）。

トークンで触れるのは、そのワークスペースに登録されたリポジトリと run だけ。発行・一覧・失効は
ワークスペースの owner だけ（approver・member・非メンバーは「無い」と同じ扱い＝呼び出し側は 404）。
平文は発行時に 1 回だけ返し、保存するのは SHA-256 だけ（高エントロピーのランダム値なので低速
ハッシュは要らない）。呼び出し側は `async with session_factory() as s, s.begin():` で包む。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import McpTokenRecord
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.mcp_tokens import McpTokenRepository
from speclino.domain.a2a import TokenIdentity
from speclino.domain.workspace import IssuedMcpToken, McpTokenInfo
from speclino.services.workspaces import WorkspaceNotFound, require_owner

TOKEN_PREFIX = "slmcp_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(session: AsyncSession, *, slug: str, name: str, actor_uid: str) -> IssuedMcpToken:
    await require_owner(session, slug, actor_uid)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = await McpTokenRepository(session).create(
        team=slug, name=name, token_hash=hash_token(token), created_by=actor_uid
    )
    await AuditRepository(session).record(
        actor_uid, "mcp_token.issue", slug, "issued", {"token_id": str(record.id), "name": name}
    )
    return IssuedMcpToken(info=_info(record), token=token)


async def list_tokens(session: AsyncSession, *, slug: str, actor_uid: str) -> list[McpTokenInfo]:
    await require_owner(session, slug, actor_uid)
    return [_info(r) for r in await McpTokenRepository(session).list_for_team(slug)]


async def revoke(session: AsyncSession, *, slug: str, token_id: uuid.UUID, actor_uid: str) -> None:
    """失効させる。別のワークスペースのトークン・失効済みも `WorkspaceNotFound`（404）。"""
    await require_owner(session, slug, actor_uid)
    if not await McpTokenRepository(session).revoke(token_id=token_id, team=slug):
        raise WorkspaceNotFound(slug)
    await AuditRepository(session).record(
        actor_uid, "mcp_token.revoke", slug, "revoked", {"token_id": str(token_id)}
    )


async def authenticate_identity(session: AsyncSession, token: str) -> TokenIdentity | None:
    """有効なトークンの身元（ワークスペースとトークン ID）。無い・失効済みは None。"""
    if not token.startswith(TOKEN_PREFIX):
        return None
    repo = McpTokenRepository(session)
    record = await repo.find_active(hash_token(token))
    if record is None:
        return None
    await repo.touch(record.id)
    return TokenIdentity(team=record.team, token_id=record.id)


async def authenticate(session: AsyncSession, token: str) -> str | None:
    """有効なトークンならそのワークスペースの slug。無い・失効済みなら None（区別しない）。"""
    identity = await authenticate_identity(session, token)
    return identity.team if identity else None


def _info(record: McpTokenRecord) -> McpTokenInfo:
    return McpTokenInfo(
        id=record.id,
        name=record.name,
        created_by=record.created_by,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
    )


__all__ = [
    "TOKEN_PREFIX",
    "WorkspaceNotFound",
    "authenticate",
    "authenticate_identity",
    "hash_token",
    "issue",
    "list_tokens",
    "revoke",
]
