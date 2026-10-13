"""knowledge registry

Revision ID: 0010
Revises: 0008
Create Date: 2026-10-05 12:00:00.000000

追加だけ（S62）。`policy_revisions` は変えない。
旧コードは新しい表を読まない。S61 縮小版が 0009 を入れる場合は、マージ時に
down_revision を合わせる。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = postgresql.ENUM("skill", "instructions", "spec", name="artifact_kind", create_type=False)
_STATUS = postgresql.ENUM(
    "draft", "active", "superseded", name="artifact_status", create_type=False
)


def _now() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    _KIND.create(bind, checkfirst=True)
    _STATUS.create(bind, checkfirst=True)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        _now(),
        sa.UniqueConstraint("workspace", "kind", "name", name="uq_artifacts_workspace_kind_name"),
    )
    op.create_index("ix_artifacts_workspace", "artifacts", ["workspace"])
    op.create_table(
        "artifact_revisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("artifact_id", sa.UUID(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", _STATUS, nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("approver", sa.String(), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        _now(),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "artifact_id", "revision", name="uq_artifact_revisions_artifact_revision"
        ),
    )
    op.create_index("ix_artifact_revisions_artifact_id", "artifact_revisions", ["artifact_id"])
    for status in ("active", "draft"):
        op.create_index(
            f"uq_artifact_revisions_artifact_{status}",
            "artifact_revisions",
            ["artifact_id"],
            unique=True,
            postgresql_where=sa.text(f"status = '{status}'"),
        )
    op.create_table(
        "bundles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("policy_hash", sa.String(), nullable=True),
        sa.Column("members", postgresql.JSONB(), nullable=False),
        sa.Column("scope_hash", sa.String(), nullable=False),
        sa.Column("signature", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace", "generation", name="uq_bundles_workspace_generation"),
    )
    op.create_table(
        "sync_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("team", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_by", sa.String(), nullable=False),
        _now(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_tokens_team", "sync_tokens", ["team"])
    op.create_table(
        "knowledge_fetches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("token_id", sa.UUID(), nullable=True),
        sa.Column("uid", sa.String(), nullable=True),
        sa.Column("bundle_generation", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("artifact_ids", postgresql.JSONB(), nullable=False),
        _now(),
    )
    op.create_index("ix_knowledge_fetches_workspace", "knowledge_fetches", ["workspace"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_knowledge_fetches_workspace", table_name="knowledge_fetches")
    op.drop_table("knowledge_fetches")
    op.drop_index("ix_sync_tokens_team", table_name="sync_tokens")
    op.drop_table("sync_tokens")
    op.drop_table("bundles")
    for status in ("draft", "active"):
        op.drop_index(f"uq_artifact_revisions_artifact_{status}", table_name="artifact_revisions")
    op.drop_index("ix_artifact_revisions_artifact_id", table_name="artifact_revisions")
    op.drop_table("artifact_revisions")
    op.drop_index("ix_artifacts_workspace", table_name="artifacts")
    op.drop_table("artifacts")
    bind = op.get_bind()
    _STATUS.drop(bind, checkfirst=True)
    _KIND.drop(bind, checkfirst=True)
