"""a2a tasks and agent supplements

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04 18:00:00.000000

追加だけ（S59）。旧コードは新しい表を読まない。`a2a_tasks` は a2a-sdk の TaskModel
（`a2a.server.models.TaskMixin`）と同じ列で、DatabaseTaskStore(create_table=False,
table_name="a2a_tasks") が読み書きする。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("context_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("status", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("protocol_version", sa.String(16), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )
    op.create_index("ix_a2a_tasks_id", "a2a_tasks", ["id"])
    op.create_index("idx_a2a_tasks_owner_last_updated", "a2a_tasks", ["owner", "last_updated"])
    op.create_table(
        "agent_supplements",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("review_runs.id"), nullable=False),
        sa.Column("requirement_id", sa.String(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_id", sa.UUID(), sa.ForeignKey("mcp_tokens.id"), nullable=True),
        sa.Column("a2a_task_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_supplements_run_id", "agent_supplements", ["run_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_agent_supplements_run_id", table_name="agent_supplements")
    op.drop_table("agent_supplements")
    op.drop_index("idx_a2a_tasks_owner_last_updated", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_id", table_name="a2a_tasks")
    op.drop_table("a2a_tasks")
