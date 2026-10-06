"""knowledge chats

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 10:00:00.000000

追加だけ（S73、design/2026-10-06-knowledge-chat.md 3 章）。既存の表は変えない。
`knowledge_chats` は知識を AI と作る会話（持ち主だけが読む）。kind は専用の型
`knowledge_chat_kind`（既存の `artifact_kind` は広げない）。`cost_ledger` には会話の費用を
ユーザー・ワークスペースごとに数える NULL 可の列（workspace / uid / purpose）を足す。
`artifact_revisions.note_source` は改訂の説明の由来（本人の申告。`chat_summary` / `manual`）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = postgresql.ENUM(
    "spec", "skill", "instructions", "policy", name="knowledge_chat_kind", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    _KIND.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "knowledge_chats",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "workspace",
            sa.String(),
            sa.ForeignKey("teams.team", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_uid", sa.String(), nullable=False),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column(
            "artifact_id",
            sa.UUID(),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("messages", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind <> 'policy' OR artifact_id IS NULL", name="ck_knowledge_chats_policy_unbound"
        ),
    )
    op.create_index(
        "ix_knowledge_chats_workspace_owner_updated",
        "knowledge_chats",
        ["workspace", "owner_uid", "updated_at"],
    )
    op.add_column("artifact_revisions", sa.Column("note_source", sa.String(), nullable=True))
    op.add_column("cost_ledger", sa.Column("workspace", sa.String(), nullable=True))
    op.add_column("cost_ledger", sa.Column("uid", sa.String(), nullable=True))
    op.add_column("cost_ledger", sa.Column("purpose", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("cost_ledger", "purpose")
    op.drop_column("cost_ledger", "uid")
    op.drop_column("cost_ledger", "workspace")
    op.drop_column("artifact_revisions", "note_source")
    op.drop_index("ix_knowledge_chats_workspace_owner_updated", table_name="knowledge_chats")
    op.drop_table("knowledge_chats")
    _KIND.drop(op.get_bind(), checkfirst=True)
