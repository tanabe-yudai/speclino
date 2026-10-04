"""services 統合テスト共通の fixture（Postgres エンジンと後片付け）。"""

from __future__ import annotations

import json
import os
import uuid
import zlib
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from speclino.db.models import Base

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")


async def set_project_approvers(engine: AsyncEngine, repo: str, approvers: list[str]) -> None:
    """project の承認者を設定する（PR コマンドの承認者は project ごとの設定だけ。S52）。"""
    async with engine.begin() as conn:
        result = await conn.execute(
            sql_text(
                "UPDATE projects SET settings = jsonb_set(settings, '{approvers}', "
                "CAST(:approvers AS jsonb)) WHERE repo_full_name = :repo"
            ),
            {"approvers": json.dumps(approvers), "repo": repo},
        )
        assert result.rowcount == 1, f"{repo} の project が無い"


E2E_TEAM = "e2e-ws"


def repo_id_of(repo: str) -> int:
    """テスト用のリポジトリ ID（名前から決まる固定値）。webhook のペイロードもこれを使う。"""
    return zlib.crc32(repo.encode())


async def register_repo(
    engine: AsyncEngine,
    repo: str,
    members: dict[str, str] | None = None,
    team: str = E2E_TEAM,
    legacy: bool = False,
) -> None:
    """ワークスペースを作り、メンバーを入れ、repo をそのワークスペースに登録する（S53）。

    API で run が見えるのは登録済みリポジトリだけなので、API を通すテストはこれを先に呼ぶ。
    project がまだ無ければ作る（本番では webhook か管理 CLI が作る）。members は uid → role。
    dev 認証の既定の利用者は "dev"。github_repo_id は `repo_id_of(repo)`。legacy=True なら
    ID を埋めない（S53 より前に作られた行の再現）。
    """
    github_repo_id = None if legacy else repo_id_of(repo)
    members = {"dev": "member"} if members is None else members
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO teams (team, approvers) VALUES (:t, '[]') ON CONFLICT DO NOTHING"
            ),
            {"t": team},
        )
        for uid, role in members.items():
            await conn.execute(
                sql_text(
                    "INSERT INTO workspace_members (team, uid, role) VALUES (:t, :u, :r) "
                    "ON CONFLICT (team, uid) DO UPDATE SET role = EXCLUDED.role"
                ),
                {"t": team, "u": uid, "r": role},
            )
        await conn.execute(
            sql_text(
                "INSERT INTO projects (id, repo_full_name, default_branch, settings) "
                "VALUES (gen_random_uuid(), :r, 'main', '{}') ON CONFLICT DO NOTHING"
            ),
            {"r": repo},
        )
        await conn.execute(
            sql_text(
                "UPDATE projects SET team = :t, "
                "github_repo_id = COALESCE(:gid, github_repo_id) WHERE repo_full_name = :r"
            ),
            {"t": team, "r": repo, "gid": github_repo_id},
        )


async def project_id_of(engine: AsyncEngine, repo: str) -> uuid.UUID:
    """repo の project の ID（run は project の ID で作る。S53）。"""
    async with engine.connect() as conn:
        pid = (
            await conn.execute(
                sql_text("SELECT id FROM projects WHERE repo_full_name = :r"), {"r": repo}
            )
        ).scalar_one()
    return uuid.UUID(str(pid))


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert _DATABASE_URL is not None
    eng = create_async_engine(_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        # 依存順に消す（projects ← review_runs ← findings / run_events / audit_events / approvals）
        pid = "SELECT id FROM projects WHERE repo_full_name LIKE 'e2e/%' OR team LIKE 'e2e-%'"
        rid = f"SELECT id FROM review_runs WHERE project_id IN ({pid})"
        for table, col, sub in (
            ("findings", "run_id", rid),
            ("run_events", "run_id", rid),
            ("evidence", "run_id", rid),
            ("tool_executions", "run_id", rid),
            ("questions", "run_id", rid),
            ("cost_ledger", "run_id", rid),
            ("audit_events", "run_id", rid),
            ("agent_supplements", "run_id", rid),
            ("review_runs", "project_id", pid),
            ("approvals", "project_id", pid),
            ("projects", "id", pid),
        ):
            await conn.execute(sql_text(f"DELETE FROM {table} WHERE {col} IN ({sub})"))
        # チーム規約・ワークスペースのテストは e2e- で始まるチームを使う
        await conn.execute(sql_text("DELETE FROM a2a_tasks WHERE owner LIKE 'e2e-%'"))
        for table in (
            "mcp_tokens",
            "repo_link_attempts",
            "workspace_invitations",
            "policy_revisions",
            "workspace_members",
            "teams",
        ):
            await conn.execute(sql_text(f"DELETE FROM {table} WHERE team LIKE 'e2e-%'"))
    await eng.dispose()
