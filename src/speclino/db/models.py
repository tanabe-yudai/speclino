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
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
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
from speclino.domain.knowledge import ArtifactKind, ArtifactStatus
from speclino.domain.knowledge_chat import KnowledgeChatKind
from speclino.domain.policy import PolicyRevisionStatus
from speclino.domain.run import ExecState, GateStatus
from speclino.domain.workspace import WorkspaceRole

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
    __table_args__ = (
        Index(
            "ux_projects_github_repo_id",
            "github_repo_id",
            unique=True,
            postgresql_where=text("github_repo_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # GitHub のリポジトリ ID（S53）。名前は改名後に別リポジトリへ再利用されうるので、同一性は
    # ID で決める。backfill 前の既存行と、GitHub を経由しないローカルの行は NULL。
    github_repo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 登録先のワークスペース（`teams.team`）。1 リポジトリは高々 1 ワークスペース。NULL は未登録で、
    # 誰の一覧にも出ず、チーム規約も当たらない。
    team: Mapped[str | None] = mapped_column(String, ForeignKey("teams.team"), nullable=True)
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
    # PR の作成者の GitHub ユーザー ID（S70: 自己確定の照合。ログイン名は改名後に他人が取れるので
    # ID で持つ）。PR 以外・記録前の run は NULL（照合できない）
    pr_author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    # S53 から読まない（承認者は `workspace_members` の role）。列の削除は後続のマイグレーションで。
    approvers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceMember(Base):
    """`workspace_members`: ワークスペース（`teams` 行）の所属と role（S53）。

    S42 の `teams` をそのままワークスペースの表として使う（slug = `teams.team`）。別の表に
    すると、改訂操作の排他（`TeamRepository.ensure_and_lock`）が 2 つの行に割れる。
    利用者は Identity Platform の uid で持つ（`domain/workspace.AuthUser` の docstring 参照）。
    """

    __tablename__ = "workspace_members"

    team: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), primary_key=True)
    uid: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    role: Mapped[WorkspaceRole] = mapped_column(
        SAEnum(*get_args(WorkspaceRole), name="workspace_role", native_enum=True), nullable=False
    )
    # 表示用のメール（S56）。招待の受諾で入る（確認済みのメールだけ）。管理 CLI・seed で入れた
    # メンバーは NULL。認可には使わない（識別子は uid）
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceInvitation(Base):
    """`workspace_invitations`: メール宛の招待（S56、design/2026-09-27-workspaces.md）。

    リンクの平文トークンは発行時の応答で 1 回だけ返し、保存は SHA-256 だけ。期限つき・1 回限り
    （`accepted_at`）・owner が取り消せる（`revoked_at`）。監査のため行は消さない。
    `email` は小文字に正規化して持つ。
    """

    __tablename__ = "workspace_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(
        SAEnum(
            *get_args(WorkspaceRole), name="workspace_role", native_enum=True, create_type=False
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpTokenRecord(Base):
    """`mcp_tokens`: ワークスペース単位の MCP トークン（S54）。

    平文は発行時の応答で 1 回だけ返し、保存するのは SHA-256 だけ。失効は `revoked_at` を
    入れる（監査のため行は消さない）。
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RepoLinkAttempt(Base):
    """`repo_link_attempts`: リポジトリ登録の GitHub 認可の試行（S55）。

    state は平文を保存せず SHA-256 だけ。コールバックで 1 回だけ消費する（`consumed_at`）。
    `code_verifier`（PKCE）は GitHub へのトークン交換で送るので平文で持つが、10 分で期限切れになり、
    消費後は使わない。
    """

    __tablename__ = "repo_link_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    uid: Mapped[str] = mapped_column(String, nullable=False)
    team: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False)
    code_verifier: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # S73: 会話の費用をワークスペース・ユーザーごとに数える。purpose は予約の行の用途
    # （`chat_message` / `chat_summary`）。それ以外の行と精算の行は NULL
    workspace: Mapped[str | None] = mapped_column(String, nullable=True)
    uid: Mapped[str | None] = mapped_column(String, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentSupplementRecord(Base):
    """`agent_supplements`: A2A の議論で得た補足（S59）。正式な回答ではなく、検査の詳細に添える。"""

    __tablename__ = "agent_supplements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_runs.id"), nullable=False, index=True
    )
    requirement_id: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_tokens.id"), nullable=True
    )
    a2a_task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactRecord(Base):
    """`artifacts`: ワークスペースの知識（skill / instructions / spec）の 1 つ（S62）。

    中身は改訂（`artifact_revisions`）が持つ。名前は (workspace, kind) の中で一意。
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("workspace", "kind", "name", name="uq_artifacts_workspace_kind_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(
        String, ForeignKey("teams.team"), nullable=False, index=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        SAEnum(*get_args(ArtifactKind), name="artifact_kind", native_enum=True), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactRevisionRecord(Base):
    """`artifact_revisions`: 知識の 1 改訂（S62）。draft は可変、active / superseded は不変。

    `content` は kind ごとの pydantic モデル（`domain/knowledge.py`）を JSONB に入れる
    （`policy_revisions.package` と同じ理由）。artifact ごとに active・draft はそれぞれ最大 1 つ。
    """

    __tablename__ = "artifact_revisions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "revision", name="uq_artifact_revisions_artifact_revision"),
        Index(
            "uq_artifact_revisions_artifact_active",
            "artifact_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_artifact_revisions_artifact_draft",
            "artifact_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        SAEnum(*get_args(ArtifactStatus), name="artifact_status", native_enum=True),
        nullable=False,
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    approver: Mapped[str | None] = mapped_column(String, nullable=True)
    self_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str] = mapped_column(String, nullable=False, default="")
    # S73: note の由来（本人の申告。会話の要約から入れたか）。note が空なら NULL
    note_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BundleRecord(Base):
    """`bundles`: ある時点の active な改訂の集合（S62）。ワークスペースごとの連番で不変。

    `members` は `BundleMember` の一覧（JSONB）。`created_at` は署名の対象なので、DB の既定値
    ではなく作った側の値をそのまま入れる。
    """

    __tablename__ = "bundles"
    __table_args__ = (
        UniqueConstraint("workspace", "generation", name="uq_bundles_workspace_generation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String, nullable=False)
    # 署名した鍵の ID（#114、鍵の更新。0011 で追加。NULL は鍵の ID を持たない古い署名）
    key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    signature: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncTokenRecord(Base):
    """`sync_tokens`: `speclino sync` 専用のトークン（S62 で表だけ、使うのは S68）。

    読み取り専用で、1 つのワークスペースの 1 つのプロジェクト（リポジトリ）に限る。平文は
    発行時に 1 回だけ返し、保存は SHA-256 だけ。失効は `revoked_at`（`mcp_tokens` と同じ作り）。
    """

    __tablename__ = "sync_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team: Mapped[str] = mapped_column(String, ForeignKey("teams.team"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeFetchRecord(Base):
    """`knowledge_fetches`: sync・MCP からの知識の取得の記録（S62 で表だけ、書くのは S68）。

    `token_id` は `channel` が sync なら `sync_tokens`、mcp なら `mcp_tokens` の ID（どちらの表も
    指しうるので外部キーにしない）。画面からの取得などトークンの無いものは NULL で `uid` を入れる。
    """

    __tablename__ = "knowledge_fetches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(
        String, ForeignKey("teams.team"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    uid: Mapped[str | None] = mapped_column(String, nullable=True)
    bundle_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True
    )
    artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SyncPlanRecord(Base):
    """`sync_plans`: MCP の `knowledge_pull` が作った書き出し計画（S75、使い捨ての取得 URL）。

    計画の本文は MCP の応答で運ばず、ここに置いて `GET /api/sync/plans/{token}` で 1 回だけ渡す。
    保存するのはトークンの SHA-256 だけ。ワークスペース・プロジェクトに結び付け、期限
    （`expires_at`）を過ぎたもの・取得済み（`consumed_at`）のものは渡さない。ワークスペースか
    プロジェクトを消せば消える。
    """

    __tablename__ = "sync_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    workspace: Mapped[str] = mapped_column(
        String, ForeignKey("teams.team", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bundle_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmAssessmentRecord(Base):
    """`llm_assessments`: LLM 支援の規約の評価の記録（S70）。キーが同じなら LLM を呼ばない。

    `key` はキーの正規 JSON（`llm_policies.key_str`）、`key_fields` はその各欄。不変で、同じ
    (workspace, キー) は最初の記録が正本（replay はこれで再評価する）。ワークスペースごとに区切る
    （未登録のプロジェクトは `project:<id>`）。
    """

    __tablename__ = "llm_assessments"
    __table_args__ = (
        UniqueConstraint("workspace", "key", name="uq_llm_assessments_workspace_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    key_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    unit_symbol: Mapped[str] = mapped_column(String, nullable=False)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttestationRecord(Base):
    """`attestations`: 人の確定（S70）。指紋と内容の hash に結びつき、内容が変われば効かない。

    確定はプロジェクトに属し（#114）、そのプロジェクトの run にだけ効く。
    同じ (workspace, project_id, fingerprint, content_hash) は最新の処置で上書きする
    （取り消し・変更は監査に残る）。actor は SpecLino の uid、または PR コメントなら
    `github:<GitHub ユーザー ID>`。
    """

    __tablename__ = "attestations"
    __table_args__ = (
        UniqueConstraint(
            "workspace",
            "project_id",
            "fingerprint",
            "content_hash",
            name="uq_attestations_workspace_project_fingerprint_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(
        String, ForeignKey("teams.team"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    disposition: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    # 最初に確定した人（確定し直しても変えない。監査に最初と最新の両方を残すため）
    first_actor: Mapped[str] = mapped_column(String, nullable=False)
    # PR の作成者が自分で確定したか（GitHub の ID で照合、拒否はしない）。NULL は照合できない
    self_attested: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeChatRecord(Base):
    """`knowledge_chats`: 知識を AI と作る会話（S73）。持ち主だけが読み書きする。

    `messages` は `domain.knowledge_chat.ChatMessage` の配列（JSONB）。件数と大きさの上限は
    サービスで確かめる。問い合わせは必ず (workspace, owner_uid) で絞る。規約の会話
    （kind=policy）はチーム規約を指すので artifact に結び付けない（CHECK）。ワークスペースを
    消せば会話も消え、artifact が消えれば結び付けだけ外れる。
    """

    __tablename__ = "knowledge_chats"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'policy' OR artifact_id IS NULL", name="ck_knowledge_chats_policy_unbound"
        ),
        Index("ix_knowledge_chats_workspace_owner_updated", "workspace", "owner_uid", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace: Mapped[str] = mapped_column(
        String, ForeignKey("teams.team", ondelete="CASCADE"), nullable=False
    )
    owner_uid: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[KnowledgeChatKind] = mapped_column(
        SAEnum(*get_args(KnowledgeChatKind), name="knowledge_chat_kind", native_enum=True),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# a2a-sdk の DatabaseTaskStore(create_table=False, table_name="a2a_tasks") が読み書きする表。
# 列は a2a.server.models.TaskMixin と同じ（SDK が ORM で扱うのでここは `create_all` 用の宣言のみ）。
a2a_tasks = Table(
    "a2a_tasks",
    Base.metadata,
    Column("id", String(36), primary_key=True),
    Column("context_id", String(36), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("owner", String(255), nullable=True),
    Column("last_updated", DateTime, nullable=True),
    Column("status", JSON, nullable=False),
    Column("artifacts", JSON, nullable=True),
    Column("history", JSON, nullable=True),
    Column("protocol_version", String(16), nullable=True),
    Column("metadata", JSON, nullable=True),
    Index("ix_a2a_tasks_id", "id"),
    Index("idx_a2a_tasks_owner_last_updated", "owner", "last_updated"),
)
