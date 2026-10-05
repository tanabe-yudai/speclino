"""知識レジストリの API の応答の型（S62）。組み立ては services/knowledge_registry.py。"""

from __future__ import annotations

from datetime import datetime

from speclino.domain.common import IRModel
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


__all__ = ["ApproveResult", "ArtifactSummary"]
