"""workspaces

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 03:00:00.000000

追加だけ（旧コードは新しい表・列を読まないので、デプロイ中の旧リビジョンと共存できる）。
`teams.approvers` のデータは移さない: 承認者の識別子をメールから uid に変えるため、本人との
対応を確かめたうえで管理 CLI（`speclino workspace add-member`）から入れ直す。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

workspace_role = postgresql.ENUM("owner", "approver", "member", name="workspace_role")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("teams", sa.Column("name", sa.String(), nullable=True))
    workspace_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "workspace_members",
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="workspace_role", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team"], ["teams.team"]),
        sa.PrimaryKeyConstraint("team", "uid"),
    )
    op.create_index(op.f("ix_workspace_members_uid"), "workspace_members", ["uid"], unique=False)
    op.add_column("projects", sa.Column("github_repo_id", sa.BigInteger(), nullable=True))
    op.add_column("projects", sa.Column("team", sa.String(), nullable=True))
    op.create_foreign_key("projects_team_fkey", "projects", "teams", ["team"], ["team"])
    op.create_index(
        "ux_projects_github_repo_id",
        "projects",
        ["github_repo_id"],
        unique=True,
        postgresql_where=sa.text("github_repo_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ux_projects_github_repo_id", table_name="projects")
    op.drop_constraint("projects_team_fkey", "projects", type_="foreignkey")
    op.drop_column("projects", "team")
    op.drop_column("projects", "github_repo_id")
    op.drop_index(op.f("ix_workspace_members_uid"), table_name="workspace_members")
    op.drop_table("workspace_members")
    workspace_role.drop(op.get_bind(), checkfirst=True)
    op.drop_column("teams", "name")
