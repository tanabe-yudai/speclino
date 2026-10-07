"""db/models.py の ORM 定義の単体試験（S14）。

DB 接続なしで検証できる範囲: (1) 想定した全テーブルが `Base.metadata` に登録されている、
(2) `exec_state` / `gate_status` / `verdict` の Enum 値が domain の Literal 型
（`ExecState` / `GateStatus` / `Verdict`）と一致している。models.py は Enum を
`typing.get_args` で domain から直接組み立てているため drift しないはずだが、
その組み立て自体が壊れていないことを念のため確認する。
"""

from __future__ import annotations

from typing import get_args

from sqlalchemy import Enum as SAEnum

from speclino.db.models import Base
from speclino.domain.finding import Verdict
from speclino.domain.knowledge import ArtifactKind, ArtifactStatus
from speclino.domain.knowledge_chat import KnowledgeChatKind
from speclino.domain.policy import PolicyRevisionStatus
from speclino.domain.run import ExecState, GateStatus

EXPECTED_TABLES = {
    "projects",
    "review_runs",
    "findings",
    "evidence",
    "tool_executions",
    "run_events",
    "questions",
    "approvals",
    "audit_events",
    "webhook_deliveries",
    "cost_ledger",
    "policy_revisions",
    "teams",
    "workspace_members",
    "mcp_tokens",
    "repo_link_attempts",
    "workspace_invitations",
    "a2a_tasks",
    "agent_supplements",
    "artifacts",
    "artifact_revisions",
    "bundles",
    "sync_tokens",
    "knowledge_fetches",
    "llm_assessments",
    "attestations",
    "knowledge_chats",
    "sync_plans",
}


def test_all_tables_are_registered_on_metadata() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def _enum_values(table: str, column: str) -> set[str]:
    col_type = Base.metadata.tables[table].columns[column].type
    assert isinstance(col_type, SAEnum)
    return set(col_type.enums)


def test_exec_state_enum_matches_domain_literal() -> None:
    assert _enum_values("review_runs", "exec_state") == set(get_args(ExecState))


def test_gate_status_enum_matches_domain_literal() -> None:
    assert _enum_values("review_runs", "gate_status") == set(get_args(GateStatus))


def test_verdict_enum_matches_domain_literal() -> None:
    assert _enum_values("findings", "verdict") == set(get_args(Verdict))


def test_review_runs_has_project_pr_created_index() -> None:
    index_names = {ix.name for ix in Base.metadata.tables["review_runs"].indexes}
    assert "ix_review_runs_project_pr_created" in index_names


def test_unique_seq_constraints_present() -> None:
    tool_exec_constraints = {c.name for c in Base.metadata.tables["tool_executions"].constraints}
    run_event_constraints = {c.name for c in Base.metadata.tables["run_events"].constraints}
    assert "uq_tool_executions_run_seq" in tool_exec_constraints
    assert "uq_run_events_run_seq" in run_event_constraints


def test_policy_revision_status_enum_matches_domain_literal() -> None:
    assert _enum_values("policy_revisions", "status") == set(get_args(PolicyRevisionStatus))


def test_policy_revisions_has_team_revision_unique_constraint() -> None:
    constraints = {c.name for c in Base.metadata.tables["policy_revisions"].constraints}
    assert "uq_policy_revisions_team_revision" in constraints


def test_policy_revisions_has_partial_unique_active_index() -> None:
    index_names = {ix.name for ix in Base.metadata.tables["policy_revisions"].indexes}
    assert "uq_policy_revisions_team_active" in index_names


def test_artifact_enums_match_domain_literal() -> None:
    assert _enum_values("artifacts", "kind") == set(get_args(ArtifactKind))
    assert _enum_values("artifact_revisions", "status") == set(get_args(ArtifactStatus))


def test_knowledge_tables_have_uniqueness() -> None:
    def names(table: str) -> set[str | None]:
        t = Base.metadata.tables[table]
        return {c.name for c in t.constraints} | {ix.name for ix in t.indexes}

    assert "uq_artifacts_workspace_kind_name" in names("artifacts")
    assert {
        "uq_artifact_revisions_artifact_revision",
        "uq_artifact_revisions_artifact_active",
        "uq_artifact_revisions_artifact_draft",
    } <= names("artifact_revisions")
    assert "uq_bundles_workspace_generation" in names("bundles")


def test_knowledge_chats_columns_and_constraints() -> None:
    """S73: 会話の表。kind は専用の Enum、artifact は NULL 可（消えたら NULL）、規約は結ばない。"""
    t = Base.metadata.tables["knowledge_chats"]
    assert {c.name for c in t.columns} == {
        "id",
        "workspace",
        "owner_uid",
        "kind",
        "artifact_id",
        "title",
        "messages",
        "version",
        "created_at",
        "updated_at",
    }
    assert _enum_values("knowledge_chats", "kind") == set(get_args(KnowledgeChatKind))
    kind_type = t.columns["kind"].type
    assert isinstance(kind_type, SAEnum) and kind_type.name == "knowledge_chat_kind"
    assert t.columns["artifact_id"].nullable
    assert not t.columns["owner_uid"].nullable
    assert t.columns["title"].type.length == 120  # type: ignore[attr-defined]
    (ws_fk,) = t.columns["workspace"].foreign_keys
    assert ws_fk.target_fullname == "teams.team" and ws_fk.ondelete == "CASCADE"
    (art_fk,) = t.columns["artifact_id"].foreign_keys
    assert art_fk.target_fullname == "artifacts.id" and art_fk.ondelete == "SET NULL"
    names = {c.name for c in t.constraints} | {ix.name for ix in t.indexes}
    assert "ck_knowledge_chats_policy_unbound" in names
    assert "ix_knowledge_chats_workspace_owner_updated" in names
    index = next(ix for ix in t.indexes if ix.name == "ix_knowledge_chats_workspace_owner_updated")
    assert [c.name for c in index.columns] == ["workspace", "owner_uid", "updated_at"]


def test_cost_ledger_has_chat_columns() -> None:
    """S73: 会話の費用をユーザー・ワークスペースごとに数える列（NULL 可の追加だけ）。"""
    t = Base.metadata.tables["cost_ledger"]
    for name in ("workspace", "uid", "purpose"):
        assert t.columns[name].nullable
