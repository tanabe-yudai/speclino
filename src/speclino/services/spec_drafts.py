"""エディタのエージェントが MCP から仕様の下書きを保存する（`spec_draft_save`）。

保存するのは下書きだけで、承認は人が画面で行う（`knowledge_registry.approve`）。

- 作者はトークンを発行した人（`mcp_tokens.created_by`）。今もワークスペースのメンバーで
  なければ拒否する
- 新しい仕様は、呼び出したリポジトリだけに当てる（`scope.projects`）
- 既にある仕様は、エージェントが前提にした改訂（`spec_get` か前回の保存の番号と hash）が
  必須。今の下書き・最新の改訂と違えば `DraftConflict`（人の下書きを黙って上書きしない。
  エラーに今の hash を載せないので、エージェントは読んでいない下書きを上書きできない）。
  本文だけを差し替え、scope・domain・旧形式の IR は前の改訂から引き継ぐ。呼び出したリポジトリに
  当たらない仕様は「無い」と同じに扱う
- 監査には `knowledge.draft`（作者）に加えて、どのトークンで保存したかを残す
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import WorkspaceMember
from speclino.db.repositories.artifacts import ArtifactRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.mcp_tokens import McpTokenRepository
from speclino.domain.knowledge import ArtifactRevision, ArtifactScope, SpecContent
from speclino.services import knowledge_registry as kr
from speclino.services.policy_governance import DraftConflict
from speclino.services.workspaces import WorkspaceNotFound

_MCP_SAVE = "mcp.spec_draft_save"


class BaseRequired(ValueError):
    """既にある仕様を、前提にした改訂の番号と hash なしで保存しようとした。"""


def _applies_to(scope: ArtifactScope, project_id: uuid.UUID) -> bool:
    return scope.workspace or str(project_id) in scope.projects


async def save(
    session: AsyncSession,
    *,
    workspace: str,
    project_id: uuid.UUID,
    name: str,
    markdown: str,
    token_id: uuid.UUID,
    note: str = "",
    expected_revision: int | None = None,
    expected_hash: str | None = None,
) -> ArtifactRevision:
    """仕様の下書きを作るか保存する。呼び出し側が 1 トランザクションに包む。

    トークンが失効した・発行した人がメンバーでなくなったときは `WorkspaceNotFound`。
    """
    # トークンとメンバーの行をロックして確かめる（失効・メンバーから外す操作は保存の後まで待つ）
    token = await McpTokenRepository(session).lock_active(token_id=token_id, team=workspace)
    if token is None:
        raise WorkspaceNotFound(workspace)
    author = token.created_by
    member = select(WorkspaceMember.uid).where(
        WorkspaceMember.team == workspace, WorkspaceMember.uid == author
    )
    if (await session.execute(member.with_for_update())).scalar_one_or_none() is None:
        raise WorkspaceNotFound(workspace)
    repo = ArtifactRepository(session)
    artifact = await repo.find(workspace, "spec", name)
    if artifact is None:
        rev = await kr.create_draft(
            session,
            workspace=workspace,
            kind="spec",
            name=name,
            content=SpecContent(
                kind="spec",
                markdown=markdown,
                scope=ArtifactScope(projects=[str(project_id)]),
            ),
            author=author,
            note=note,
        )
    else:
        base = await repo.current_draft(artifact.id) or await repo.latest(artifact.id)
        if base is None or not isinstance(base.content, SpecContent):
            raise kr.ArtifactNotFound(str(artifact.id))
        if not _applies_to(base.content.scope, project_id):
            raise kr.ArtifactNotFound(str(artifact.id))
        if expected_revision is None or expected_hash is None:
            raise BaseRequired(
                f"{name} は既にある。spec_get か前回の保存が返した revision と content_hash を渡す"
            )
        if base.revision != expected_revision or base.content_hash != expected_hash:
            # 今の hash は返さない（読んでいない下書きを、返した値で上書きさせない）
            raise DraftConflict(
                f"{name} の今の改訂は {base.revision}（{base.status}、作者 {base.author}）で、"
                "渡した改訂と違う。画面で今の下書きを人に確かめてもらう"
            )
        rev = await kr.update_draft(
            session,
            artifact_id=str(artifact.id),
            expected_revision=expected_revision,
            expected_hash=expected_hash,
            content=SpecContent(
                kind="spec",
                markdown=markdown,
                domain=base.content.domain,
                legacy_ir=base.content.legacy_ir,
                scope=base.content.scope,
            ),
            author=author,
            note=note,
        )
    await AuditRepository(session).record(
        author,
        _MCP_SAVE,
        f"{workspace}/{rev.artifact_id}@{rev.revision}",
        "saved",
        {"token_id": str(token_id), "name": name, "content_hash": rev.content_hash},
    )
    return rev


__all__ = ["BaseRequired", "DraftConflict", "WorkspaceNotFound", "save"]
