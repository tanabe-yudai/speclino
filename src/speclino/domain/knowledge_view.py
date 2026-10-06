"""知識レジストリの API の応答の型（S62）。組み立ては services/knowledge_registry.py。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.finding import Verdict
from speclino.domain.knowledge import ArtifactKind, ArtifactRevision, Bundle


class ArtifactSummary(IRModel):
    artifact_id: str
    workspace: str
    kind: ArtifactKind
    name: str
    active_revision: int | None = None
    draft_revision: int | None = None
    created_at: datetime


class ApproveResult(IRModel):
    """承認の結果。`revision.self_approved` で作者本人（または下書きを直した人）の承認が分かる。"""

    revision: ArtifactRevision
    bundle: Bundle


__all__ = ["ApproveResult", "ArtifactSummary", "CutoverProjectStatus"]


class CutoverProjectStatus(IRModel):
    """知識レジストリへの切り替えの進み具合の 1 プロジェクト分（`speclino cutover-status`）。"""

    workspace: str
    project_id: str
    repo_full_name: str
    bundle_generation: int | None = Field(
        description="ワークスペースの今の bundle（無ければ None）"
    )
    approved_specs: list[str] = Field(
        default_factory=list,
        description="今の bundle でこのプロジェクトに当たる承認済みの仕様の名前",
    )
    bundle_problem: str | None = Field(
        default=None, description="bundle をこのプロジェクトに当てられない理由（署名など）"
    )
    active_sync_tokens: int = Field(ge=0, description="失効していない sync トークンの数")
    latest_run_id: str | None = Field(default=None, description="最新の完了した検査")
    lock_verdict: Verdict | None = Field(
        default=None, description="最新の完了した検査の KNOWLEDGE-LOCK-001 の判定（無ければ None）"
    )
    ready: bool = Field(
        description="承認済みの仕様・sync トークン・lock の一致した最新の検査がそろっている"
    )
