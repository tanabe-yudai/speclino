"""`services.workspace_admin` の統合試験（S53、design/2026-09-27-workspaces.md）。

実 PostgreSQL が必要。`SPECLINO_DATABASE_URL` が未設定ならモジュール全体を skip する
（他の統合テストと同じ理由）。後始末は `conftest.py` の `engine` フィクスチャが
`team LIKE 'e2e-%'` / `repo_full_name LIKE 'e2e/%'` で行うため、このテストが作る
ワークスペース・リポジトリ名は必ずその接頭辞を使う。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.db.models import AuditEvent, Project
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.workspace import BackfillResult, WorkspaceSummary
from speclino.services import workspace_admin

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _DATABASE_URL is None,
        reason="SPECLINO_DATABASE_URL が未設定のため DB 統合テストを skip する",
    ),
]


def _slug() -> str:
    return f"e2e-admin-{uuid.uuid4().hex[:8]}"


def _repo() -> str:
    return f"e2e/admin-{uuid.uuid4().hex[:8]}"


async def _audit_rows(
    factory: async_sessionmaker[AsyncSession], action: str, target: str
) -> list[AuditEvent]:
    async with factory() as session:
        stmt = select(AuditEvent).where(AuditEvent.action == action, AuditEvent.target == target)
        return list((await session.execute(stmt)).scalars())


async def test_create_workspace_rejects_duplicate(engine: AsyncEngine) -> None:
    slug = _slug()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug, name="Tanabe", owner_uid="u1")

    with pytest.raises(ValueError):
        async with factory() as session, session.begin():
            await workspace_admin.create_workspace(session, slug=slug, name=None, owner_uid="u2")

    summaries = {w.slug: w for w in await _list(factory)}
    ws = summaries[slug]
    assert ws.name == "Tanabe"
    assert {m.uid: m.role for m in ws.members} == {"u1": "owner"}

    rows = await _audit_rows(factory, "workspace.create", slug)
    assert len(rows) == 1
    assert rows[0].decision == "created"
    assert rows[0].actor == "admin-cli"


async def test_create_workspace_adopts_a_memberless_teams_row(engine: AsyncEngine) -> None:
    """S42 の規約操作で作られた（メンバーのいない）teams 行は、ワークスペースとして引き取れる。"""
    slug = _slug()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await TeamRepository(session).ensure_and_lock(slug)

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug, name="Adopted", owner_uid="u1")

    ws = {w.slug: w for w in await _list(factory)}[slug]
    assert ws.name == "Adopted"
    assert {m.uid: m.role for m in ws.members} == {"u1": "owner"}


async def test_add_member_replaces_role(engine: AsyncEngine) -> None:
    slug = _slug()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug, name=None, owner_uid="owner1")
    async with factory() as session, session.begin():
        await workspace_admin.add_member(session, slug=slug, uid="u2", role="member")
    async with factory() as session, session.begin():
        await workspace_admin.add_member(session, slug=slug, uid="u2", role="approver")

    summaries = {w.slug: w for w in await _list(factory)}
    roles = {m.uid: m.role for m in summaries[slug].members}
    assert roles == {"owner1": "owner", "u2": "approver"}

    rows = await _audit_rows(factory, "workspace.add_member", slug)
    # member → approver で 2 回呼んでいるので監査も 2 件残る。
    assert [r.decision for r in rows] == ["set", "set"]


async def test_set_pr_approvers_replaces_the_list_and_keeps_other_settings(
    engine: AsyncEngine,
) -> None:
    """PR コメントの承認者（GitHub ログイン名）を置き換え、監査に前後を残す。"""
    repo = _repo()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(
            Project(
                repo_full_name=repo,
                default_branch="main",
                settings={"approvers": ["old"], "other": 1},
            )
        )
    async with factory() as session, session.begin():
        await workspace_admin.set_pr_approvers(session, repo_full_name=repo, logins=["alice"])

    async with factory() as session:
        project = await ProjectRepository(session).get_by_repo(repo)
    assert project is not None
    assert project.settings == {"approvers": ["alice"], "other": 1}
    rows = await _audit_rows(factory, "project.set_pr_approvers", repo)
    assert rows[0].detail == {"before": ["old"], "after": ["alice"]}

    with pytest.raises(ValueError, match="project が存在しない"):
        async with factory() as session, session.begin():
            await workspace_admin.set_pr_approvers(session, repo_full_name="e2e/nope", logins=[])


async def test_add_member_rejects_missing_workspace(engine: AsyncEngine) -> None:
    slug = _slug()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with pytest.raises(ValueError):
        async with factory() as session, session.begin():
            await workspace_admin.add_member(session, slug=slug, uid="u1", role="member")


async def test_add_project_rejects_null_github_repo_id(engine: AsyncEngine) -> None:
    slug = _slug()
    repo = _repo()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug, name=None, owner_uid="owner1")
        session.add(Project(repo_full_name=repo, default_branch="main", settings={}))

    with pytest.raises(ValueError):
        async with factory() as session, session.begin():
            await workspace_admin.add_project(session, slug=slug, repo_full_name=repo)


async def test_add_project_rejects_double_registration_and_is_idempotent(
    engine: AsyncEngine,
) -> None:
    slug1 = _slug()
    slug2 = _slug()
    repo = _repo()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    github_repo_id = uuid.uuid4().int % 1_000_000_000

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug1, name=None, owner_uid="owner1")
        await workspace_admin.create_workspace(session, slug=slug2, name=None, owner_uid="owner2")
        session.add(
            Project(
                repo_full_name=repo,
                default_branch="main",
                settings={},
                github_repo_id=github_repo_id,
            )
        )

    async with factory() as session, session.begin():
        await workspace_admin.add_project(session, slug=slug1, repo_full_name=repo)

    # 別ワークスペースからの登録は拒否する。
    with pytest.raises(ValueError):
        async with factory() as session, session.begin():
            await workspace_admin.add_project(session, slug=slug2, repo_full_name=repo)

    # 同じワークスペースへの再登録は冪等に成功する（監査は増えない）。
    async with factory() as session, session.begin():
        await workspace_admin.add_project(session, slug=slug1, repo_full_name=repo)

    async with factory() as session:
        registered = await ProjectRepository(session).get_by_repo(repo)
    assert registered is not None
    assert registered.team == slug1

    rows = await _audit_rows(factory, "workspace.add_project", slug1)
    assert len(rows) == 1
    assert rows[0].decision == "added"
    assert rows[0].detail["repo"] == repo


async def test_backfill_repo_ids_fills_and_isolates(engine: AsyncEngine) -> None:
    slug = _slug()
    repo_ok = _repo()
    repo_missing = _repo()
    repo_conflict = _repo()
    repo_existing = _repo()
    repo_stale = f"{repo_ok}#stale-abcd1234"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    existing_id = uuid.uuid4().int % 1_000_000_000

    async with factory() as session, session.begin():
        await workspace_admin.create_workspace(session, slug=slug, name=None, owner_uid="owner1")
        session.add_all(
            [
                Project(repo_full_name=repo_ok, default_branch="main", settings={}),
                # backfill 前の legacy 行を模す: team は既に入っているが github_repo_id はまだ無い。
                Project(repo_full_name=repo_missing, default_branch="main", settings={}, team=slug),
                Project(
                    repo_full_name=repo_conflict, default_branch="main", settings={}, team=slug
                ),
                Project(
                    repo_full_name=repo_existing,
                    default_branch="main",
                    settings={},
                    github_repo_id=existing_id,
                ),
                Project(repo_full_name=repo_stale, default_branch="main", settings={}),
            ]
        )

    host = LocalCodeHost(repos={}, repo_ids={repo_ok: existing_id + 1, repo_conflict: existing_id})

    async with factory() as session, session.begin():
        results = await workspace_admin.backfill_repo_ids(session, host)

    by_repo = {r.repo: r for r in results}
    assert repo_stale not in by_repo, "#stale- を含む行は対象外のはず"
    assert repo_existing not in by_repo, "github_repo_id が既にある行は対象外のはず"
    assert by_repo[repo_ok] == BackfillResult(repo=repo_ok, action="filled")
    assert by_repo[repo_missing] == BackfillResult(
        repo=repo_missing, action="isolated", reason="not_found"
    )
    assert by_repo[repo_conflict] == BackfillResult(
        repo=repo_conflict, action="isolated", reason="id_conflict"
    )

    async with factory() as session:
        refreshed_ok = await ProjectRepository(session).get_by_repo(repo_ok)
        refreshed_missing = await ProjectRepository(session).get_by_repo(repo_missing)
        refreshed_conflict = await ProjectRepository(session).get_by_repo(repo_conflict)
    assert refreshed_ok is not None and refreshed_ok.github_repo_id == existing_id + 1
    assert refreshed_missing is not None and refreshed_missing.team is None
    assert refreshed_conflict is not None
    assert refreshed_conflict.team is None
    assert refreshed_conflict.github_repo_id is None

    filled_rows = await _audit_rows(factory, "workspace.backfill_repo_ids", repo_ok)
    assert [r.decision for r in filled_rows] == ["filled"]
    isolated_rows = await _audit_rows(factory, "workspace.backfill_repo_ids", repo_missing)
    assert [r.decision for r in isolated_rows] == ["isolated"]
    assert isolated_rows[0].detail["reason"] == "not_found"


async def _list(factory: async_sessionmaker[AsyncSession]) -> list[WorkspaceSummary]:
    async with factory() as session:
        return await workspace_admin.list_workspaces(session)
