"""user profiles

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12 12:00:00.000000

追加だけ。既存の表は変えない。`user_profiles` は利用者の表示名とメール（表示用）。
旧コードはこの表を読まないので、新スキーマのまま旧コードが動く。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_profiles",
        sa.Column("uid", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("user_profiles")
