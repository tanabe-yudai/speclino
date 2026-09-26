"""SQLAlchemy 2.0 の ORM モデル定義（S14）。

テーブル定義は `design/2026-09-22-hackathon-plan.md` の「データモデル（MVP）」節に従う。
主キーは UUID（Python 側で `uuid.uuid4` を割り当てる。DB 拡張への依存を避けるため）。
JSON カラムは PostgreSQL の JSONB。時刻カラムは timezone-aware（timestamptz）。

`exec_state` / `gate_status` / `verdict` の Enum 値は、domain 層の `Literal` 型
（`speclino.domain.run.ExecState` / `GateStatus`、`speclino.domain.finding.Verdict`）から
`typing.get_args` で取り出して DB の Enum を構築する。値を手で書き写すと drift するため、
単一の情報源（domain）から生成することでズレを構造的に防ぐ（tests/unit/db/test_models.py で
念のため一致を検証する）。

ORM の `relationship()` は使わない。repositories は FK カラムを介した明示的なクエリで
関連データを取得する（MVP に不要な機能を足さない）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from speclino.domain.finding import Verdict
from speclino.domain.policy import PolicyRevisionStatus
from speclino.domain.run import ExecState, GateStatus

# DB 固有の Enum（domain 側に対応する Literal がまだ無いもの）。
EvidenceOrigin = Literal["static_analysis", "runtime_trace", "human"]
ToolDecision = Literal["allowed", "denied"]
QuestionStatus = Literal["open", "answered", "stale"]
ApprovalKind = Literal["approve_spec", "answer", "attest"]


class Base(DeclarativeBase):
    """全 ORM モデルの共通ベース。"""


class Project(Base):
    """`projects`: GitHub リポジトリ単位の設定。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ReviewRun(Base):
    """`review_runs`: 1 回のレビュー実行（exec_state と gate_status を分離）。"""

    __tablename__ = "review_runs"
    __table_args__ = (
        Index("ix_review_runs_project_pr_created", "project_id", "pr_number", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    head_sha: Mapped[str] = mapped_column(String, nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    exec_state: Mapped[ExecState] = mapped_column(
        SAEnum(*get_args(ExecState), name="exec_state", native_enum=True),
        nullable=False,
        default="queued",
    )
    gate_status: Mapped[GateStatus | None] = mapped_column(
        SAEnum(*get_args(GateStatus), name="gate_status", native_enum=True),
        nullable=True,
    )
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=True
    )
    check_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    artifacts_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # run_events.seq をアトミックに採番するためのカウンタ（run_events.py の docstring 参照）。
    next_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FindingRecord(Base):
    """`findings`: Finding（domain）の永続化行。payload に Finding 全体を JSONB で保存する。

    `Finding` という名前は `speclino.domain.finding.Finding` と衝突するため、
    ORM 側は `FindingRecord` と命名する。
    """

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    requirement_id: Mapped[str] = mapped_column(String, nullable=False)
    behavior_id: Mapped[str] = mapped_column(String, nullable=False)
    verdict: Mapped[Verdict] = mapped_column(
        SAEnum(*get_args(Verdict), name="verdict", native_enum=True), nullable=False
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    escalation_state: Mapped[str] = mapped_column(String, nullable=False, default="none")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Evidence(Base):
    """`evidence`: Finding を裏付ける根拠（静的解析・実行トレース・人手）。"""

    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    producer: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[EvidenceOrigin] = mapped_column(
        SAEnum(*get_args(EvidenceOrigin), name="evidence_origin", native_enum=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    blob_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ToolExecution(Base):
    """`tool_executions`: エージェントの ToolGateway 経由の呼出し記録（監査用）。"""

    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_tool_executions_run_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    args_masked: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    args_hash: Mapped[str] = mapped_column(String, nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    decision: Mapped[ToolDecision] = mapped_column(
        SAEnum(*get_args(ToolDecision), name="tool_decision", native_enum=True),
        nullable=False,
    )
    deny_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunEvent(Base):
    """`run_events`: run 内で単調増加する seq を持つタイムライン。"""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Question(Base):
    """`questions`: `human_ask` が作る構造化質問と回答。"""

    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    requirement_id: Mapped[str | None] = mapped_column(String, nullable=True)
    head_sha: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[QuestionStatus] = mapped_column(
        SAEnum(*get_args(QuestionStatus), name="question_status", native_enum=True),
        nullable=False,
        default="open",
    )
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Approval(Base):
    """`approvals`: approve_spec / answer / attest の承認記録。"""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    kind: Mapped[ApprovalKind] = mapped_column(
        SAEnum(*get_args(ApprovalKind), name="approval_kind", native_enum=True), nullable=False
    )
    target: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    approver: Mapped[str] = mapped_column(String, nullable=False)
    comment_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    """`audit_events`: 権限検査・エスカレーション等の監査ログ。"""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=True, index=True
    )


class WebhookDelivery(Base):
    """`webhook_deliveries`: GitHub webhook の delivery_id で重複排除する。"""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TeamRecord(Base):
    """`teams`: チーム単位の統治設定（統治の穴修正、design/2026-09-26-team-policies-and-dx.md）。

    承認者の許可集合はここに持つ。`policy_revisions`（改訂）には持たせない。改訂の中に
    承認者リストを入れると、改訂を作る人（author）が自分を承認者に足せてしまうため、
    改訂とは別のチーム単位の受け皿に分離する（`services/policy_governance.py` の `approve`
    が検証に使う）。
    """

    __tablename__ = "teams"

    team: Mapped[str] = mapped_column(String, primary_key=True)
    approvers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyRevisionRecord(Base):
    """`policy_revisions`: チーム規約の 1 改訂（S42、design/2026-09-26-team-policies-and-dx.md）。

    `policy_rules` という別テーブルには正規化しない。ルールは常に改訂単位でまとめて
    読む・比較する（`active_revision` は改訂全体を返し、run-manifest も改訂番号ごと
    まとめて replay する）ため、行に分けても JOIN や個別更新の利点が生きない。
    `PolicyPackage`（pydantic）をそのまま JSONB に入れるほうが domain と DB の型が
    1つに保てる（`review_runs.manifest` も同じ理由で構造化 JSON を JSONB に入れている）。

    `status` の一意性は 2 段: (team, revision) は UNIQUE、かつ team ごとに
    status='active' な行は最大 1 つ（部分一意インデックス）。
    """

    __tablename__ = "policy_revisions"
    __table_args__ = (
        UniqueConstraint("team", "revision", name="uq_policy_revisions_team_revision"),
        Index(
            "uq_policy_revisions_team_active",
            "team",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PolicyRevisionStatus] = mapped_column(
        SAEnum(*get_args(PolicyRevisionStatus), name="policy_revision_status", native_enum=True),
        nullable=False,
    )
    package: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    approver: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CostLedgerEntry(Base):
    """`cost_ledger`: 日次の費用ガード用の台帳。"""

    __tablename__ = "cost_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=True
    )
    estimated_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
