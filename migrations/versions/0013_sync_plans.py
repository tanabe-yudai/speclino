"""sync plans

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 18:00:00.000000

追加だけ（S75）。既存の表は変えない。
`sync_plans` は MCP の `knowledge_pull` が作った書き出し計画。本文は MCP の応答で運ばず、
使い捨ての取得 URL（トークンの SHA-256 だけを保存）で 1 回だけ渡す。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sync_plans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "workspace",
            sa.String(),
            sa.ForeignKey("teams.team", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("bundle_generation", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("token_id", sa.UUID(), nullable=True),
        sa.Column("artifact_ids", postgresql.JSONB(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_plans_workspace", "sync_plans", ["workspace"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_sync_plans_workspace", table_name="sync_plans")
    op.drop_table("sync_plans")
