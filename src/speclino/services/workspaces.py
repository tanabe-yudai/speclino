"""ワークスペースによる可視範囲（S53、design/2026-09-27-workspaces.md の 4 章）。

UI / API で見えるのは、ログインした人が所属するワークスペースに登録された project の run だけ。
それ以外は「無い」と同じ扱いにする（呼び出し側は 404 を返し、存在を漏らさない）。
チーム規約は project の登録先（`projects.team`）から引く（worker と MCP で同じ）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, ReviewRun
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.ports.code_host import CodeHost


async def visible_runs(session: AsyncSession, uid: str, limit: int) -> list[tuple[ReviewRun, str]]:
    """uid が所属するワークスペースの run（新しい順）と、そのリポジトリ名。"""
    teams = await TeamRepository(session).teams_of(uid)
    if not teams:
        return []
    stmt = (
        select(ReviewRun, Project.repo_full_name)
        .join(Project, Project.id == ReviewRun.project_id)
        .where(Project.team.in_(teams))
        .order_by(ReviewRun.created_at.desc())
        .limit(limit)
    )
    return [(run, repo) for run, repo in (await session.execute(stmt)).all()]


async def visible_run(
    session: AsyncSession, uid: str, run_id: uuid.UUID
) -> tuple[ReviewRun, Project] | None:
    """uid から見える run とその project。無い・見えない場合はどちらも None。"""
    run = await session.get(ReviewRun, run_id)
    if run is None:
        return None
    project = await session.get(Project, run.project_id)
    if project is None or not await _is_member_of_project(session, uid, project):
        return None
    return run, project


async def visible_project(session: AsyncSession, uid: str, repo_full_name: str) -> Project | None:
    """uid が所属するワークスペースに登録されたリポジトリの project。それ以外は None。"""
    project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if project is None or not await _is_member_of_project(session, uid, project):
        return None
    return project


async def _is_member_of_project(session: AsyncSession, uid: str, project: Project) -> bool:
    return project.team is not None and project.team in await TeamRepository(session).teams_of(uid)


async def repository_mismatch(
    code_host: CodeHost, repo_full_name: str, github_repo_id: int | None
) -> str | None:
    """今その名前で取れるリポジトリが、project のリポジトリ ID と一致するか。違えば理由を返す。

    スナップショットの取得・Check の作成は名前で行う。名前は改名後に別リポジトリへ再利用
    されうるので、名前で取得する前に ID を照らし合わせ、違えば別リポジトリの中身を読まない
    （worker と MCP の両方がこれを使う）。ID を持たない project（GitHub を経由しないローカルの
    行）は照合できないので通す。
    """
    if github_repo_id is None:
        return None
    try:
        current = await code_host.repository_id(repo_full_name)
    except Exception as e:  # noqa: BLE001 - 照合できないなら読まない（安全側）
        return f"リポジトリ ID を確認できない: {type(e).__name__}: {e}"
    if current != github_repo_id:
        return (
            f"{repo_full_name} のリポジトリ ID が登録時と違う"
            f"（登録 {github_repo_id}、現在 {current}）。改名・削除の可能性がある"
        )
    return None


__all__ = [
    "repository_mismatch",
    "visible_project",
    "visible_run",
    "visible_runs",
]
