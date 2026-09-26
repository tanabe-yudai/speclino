"""DB repositories（S14）。各クラスは `AsyncSession` を受け取り、対応するテーブルを操作する。"""

from __future__ import annotations

from speclino.db.repositories.approvals import ApprovalRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.db.repositories.findings import FindingRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.questions import QuestionRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.db.repositories.run_events import RunEventRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.db.repositories.webhook_deliveries import WebhookDeliveryRepository

__all__ = [
    "ApprovalRepository",
    "AuditRepository",
    "CostLedgerRepository",
    "FindingRepository",
    "PolicyRevisionRepository",
    "ProjectRepository",
    "QuestionRepository",
    "ReviewRunRepository",
    "RunEventRepository",
    "TeamRepository",
    "WebhookDeliveryRepository",
]
