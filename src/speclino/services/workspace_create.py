"""利用者が自分でワークスペースを作る（画面の「自分のワークスペース」）。

作った人が owner になる。管理 CLI の `workspace_admin.create_workspace` と違い、既存の `teams` 行は
引き取らない（メンバーのいない行にも規約の改訂や知識が残りうるので、他人の行を乗っ取らせない）。
1 人が owner のワークスペースの数に上限を置き、作成は監査に残す。作ったワークスペースには
プリセットの知識（`knowledge_presets`）を承認済みで入れる。
"""

from __future__ import annotations

import re

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import TeamRecord, WorkspaceMember
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.services.knowledge_presets import install_presets

# URL（/w/:slug）と run-manifest に残る識別子。
# 英小文字・数字・ハイフンで 2〜40 文字、端はハイフン以外
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
MAX_OWNED_WORKSPACES = 5
RAISED_MAX_OWNED_WORKSPACES = 100
MAX_NAME_LENGTH = 80


class InvalidWorkspace(ValueError):
    """slug または表示名の形が正しくない（呼び出し側は 422）。"""


class WorkspaceExists(Exception):
    """同じ slug の `teams` 行が既にある（呼び出し側は 409）。"""


class WorkspaceLimitReached(Exception):
    """owner のワークスペースが上限に達している（呼び出し側は 409）。"""


def normalize_name(name: str | None) -> str | None:
    """表示名の前後の空白を除く。空なら None。長すぎる・制御文字を含むなら `InvalidWorkspace`。"""
    if name is None:
        return None
    stripped = name.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_NAME_LENGTH:
        raise InvalidWorkspace(f"表示名は {MAX_NAME_LENGTH} 文字以内にしてください")
    if any(not ch.isprintable() for ch in stripped):
        raise InvalidWorkspace("表示名に制御文字は使えません")
    return stripped


def owned_limit(uid: str, raised_uids: str) -> int:
    """`uid` が owner になれるワークスペースの数。`raised_uids` はカンマ区切りの uid。"""
    raised = {u.strip() for u in raised_uids.split(",") if u.strip()}
    return RAISED_MAX_OWNED_WORKSPACES if uid in raised else MAX_OWNED_WORKSPACES


async def create_own_workspace(
    session: AsyncSession,
    *,
    uid: str,
    slug: str,
    name: str | None,
    max_owned: int = MAX_OWNED_WORKSPACES,
) -> str | None:
    """`uid` を owner とするワークスペースを作り、正規化した表示名を返す。"""
    if not SLUG_PATTERN.fullmatch(slug) or "--" in slug:
        raise InvalidWorkspace(
            "識別子は英小文字・数字・ハイフンで 2〜40 文字にします（先頭と末尾は英小文字か数字）"
        )
    display_name = normalize_name(name)
    # 件数の確認と作成を利用者ごとに直列化する（同時の作成で上限をすり抜けさせない）
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"workspace_create:{uid}"},
    )
    owned = await session.execute(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.uid == uid, WorkspaceMember.role == "owner")
    )
    if owned.scalar_one() >= max_owned:
        raise WorkspaceLimitReached(slug)
    inserted = await session.execute(
        pg_insert(TeamRecord)
        .values(team=slug, approvers=[], name=display_name)
        .on_conflict_do_nothing(index_elements=[TeamRecord.team])
        .returning(TeamRecord.team)
    )
    if inserted.scalar_one_or_none() is None:
        raise WorkspaceExists(slug)
    await TeamRepository(session).set_member(slug, uid, "owner")
    await AuditRepository(session).record(
        actor=uid,
        action="workspace.create",
        target=slug,
        decision="created",
        detail={"name": display_name, "via": "self_service"},
    )
    await install_presets(session, slug)
    return display_name
