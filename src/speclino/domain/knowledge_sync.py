"""知識の配信（S68、design/2026-10-05-team-knowledge-registry.md 7 章）の型。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.knowledge import Bundle, KnowledgeLock


@dataclass(frozen=True)
class SyncTokenInfo:
    """sync トークンの一覧・発行の応答に出す情報。平文もハッシュも含めない。"""

    id: uuid.UUID
    project_id: uuid.UUID
    repo_full_name: str
    name: str
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedSyncToken:
    """発行直後だけ手に入る平文のトークン（保存しない）。"""

    info: SyncTokenInfo
    token: str


@dataclass(frozen=True)
class SyncIdentity:
    """有効な sync トークンの身元。触れるのはこのワークスペースのこのプロジェクトだけ。"""

    token_id: uuid.UUID
    workspace: str
    project_id: uuid.UUID


class SyncPayload(IRModel):
    """`GET /api/sync/bundle` の応答。`files` は相対パス → 本文。

    `AGENTS.md` / `CLAUDE.md` の本文は管理区画の中身だけ（区画の外は利用者のもの）。
    """

    bundle: Bundle
    files: dict[str, str] = Field(default_factory=dict)
    lock: KnowledgeLock


__all__ = ["IssuedSyncToken", "SyncIdentity", "SyncPayload", "SyncTokenInfo"]
