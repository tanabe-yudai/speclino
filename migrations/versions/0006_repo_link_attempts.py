"""repo link attempts

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29 15:40:00.000000

追加だけ（旧コードは新しい表を読まない）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repo_link_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("state_hash", sa.String(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("repo_full_name", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team"], ["teams.team"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("repo_link_attempts")
