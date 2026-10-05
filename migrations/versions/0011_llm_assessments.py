"""llm assessments and attestations

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 18:00:00.000000

追加だけ（S70、design/2026-10-05-team-knowledge-registry.md 6.2–6.3）。既存の表は変えない。
`llm_assessments` は LLM 支援の評価の記録（キーが同じなら LLM を呼ばない・replay が読む）、
`attestations` は人の確定（ゲートの処置。verdict は書き換えない）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_assessments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("key_fields", postgresql.JSONB(), nullable=False),
        sa.Column("unit_symbol", sa.String(), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "attestations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("disposition", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace",
            "fingerprint",
            "content_hash",
            name="uq_attestations_workspace_fingerprint_hash",
        ),
    )
    op.create_index("ix_attestations_workspace", "attestations", ["workspace"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_attestations_workspace", table_name="attestations")
    op.drop_table("attestations")
    op.drop_table("llm_assessments")
