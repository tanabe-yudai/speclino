"""チーム規約の画面（S43）の応答の型。組み立ては services/policy_governance.py と routes。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from speclino.domain.common import IRModel
from speclino.domain.finding import Verdict
from speclino.domain.policy import PolicyRevision, PolicyRevisionStatus


class RevisionSummary(IRModel):
    revision: int
    status: PolicyRevisionStatus
    author: str
    approver: str | None
    note: str
    rule_count: int
    created_at: datetime
    approved_at: datetime | None


class AuditEntry(IRModel):
    ts: datetime
    actor: str
    action: str
    target: str
    decision: str
    detail: dict[str, Any]


class RuleResult(IRModel):
    status: Verdict | None
    projects: int
    failing_projects: int


class PoliciesView(IRModel):
    active: PolicyRevision | None
    draft: PolicyRevision | None
    draft_hash: str | None
    can_approve: bool
    verifiers: dict[str, str]
    results: dict[str, RuleResult]
