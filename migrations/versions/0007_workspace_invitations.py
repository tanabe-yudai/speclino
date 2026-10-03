"""workspace invitations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04 10:00:00.000000

追加だけ（S56）。旧コードは新しい表と `workspace_members.email` を読まない。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("workspace_members", sa.Column("email", sa.String(), nullable=True))
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="workspace_role", create_type=False),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team"], ["teams.team"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_workspace_invitations_team"), "workspace_invitations", ["team"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_workspace_invitations_team"), table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
    op.drop_column("workspace_members", "email")
