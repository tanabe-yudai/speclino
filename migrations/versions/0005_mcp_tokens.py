"""mcp tokens

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28 14:30:00.000000

追加だけ（旧コードは新しい表を読まない）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team"], ["teams.team"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_mcp_tokens_team"), "mcp_tokens", ["team"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_mcp_tokens_team"), table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
