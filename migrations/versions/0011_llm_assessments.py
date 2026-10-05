"""llm assessments and attestations

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 18:00:00.000000

追加だけ（S70、design/2026-10-05-team-knowledge-registry.md 6.2–6.3）。既存の表は変えない。
`llm_assessments` は LLM 支援の評価の記録（キーが同じなら LLM を呼ばない・replay が読む）、
`attestations` は人の確定（ゲートの処置。verdict は書き換えない）。確定はプロジェクトに属する
（#114: 同じ指紋でも別のプロジェクトの run には効かせない）。評価の記録はワークスペース
ごとに区切る。`review_runs.pr_author_id`（PR の作成者の GitHub ユーザー ID。自己確定の照合）は
NULL 可の列の追加だけ。`bundles.key_id`（署名した鍵の ID。#114 の鍵の更新）も NULL 可の列の
追加だけ。
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
        sa.Column("workspace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("key_fields", postgresql.JSONB(), nullable=False),
        sa.Column("unit_symbol", sa.String(), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace", "key", name="uq_llm_assessments_workspace_key"),
    )
    op.create_table(
        "attestations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace", sa.String(), sa.ForeignKey("teams.team"), nullable=False),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("disposition", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("first_actor", sa.String(), nullable=False),
        sa.Column("self_attested", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace",
            "project_id",
            "fingerprint",
            "content_hash",
            name="uq_attestations_workspace_project_fingerprint_hash",
        ),
    )
    op.create_index("ix_attestations_workspace", "attestations", ["workspace"])
    op.create_index("ix_attestations_project_id", "attestations", ["project_id"])
    op.add_column("review_runs", sa.Column("pr_author_id", sa.BigInteger(), nullable=True))
    op.add_column("bundles", sa.Column("key_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bundles", "key_id")
    op.drop_column("review_runs", "pr_author_id")
    op.drop_index("ix_attestations_project_id", table_name="attestations")
    op.drop_index("ix_attestations_workspace", table_name="attestations")
    op.drop_table("attestations")
    op.drop_table("llm_assessments")
