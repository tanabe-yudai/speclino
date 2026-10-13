"""ワークスペースによる可視範囲（S53）。

UI / API で見えるのは、ログインした人が所属するワークスペースに登録された project の run だけ。
それ以外は「無い」と同じ扱いにする（呼び出し側は 404 を返し、存在を漏らさない）。
チーム規約は project の登録先（`projects.team`）から引く（worker と MCP で同じ）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project, ReviewRun, WorkspaceMember
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.workspace import WorkspaceRole
from speclino.ports.code_host import CodeHost


async def visible_runs(
    session: AsyncSession, uid: str, limit: int, workspace: str | None = None
) -> list[tuple[ReviewRun, str]]:
    """uid が所属するワークスペースの run（新しい順）と、そのリポジトリ名。

    workspace を渡すとそのワークスペースの run だけ（所属していなければ `WorkspaceNotFound`）。
    全ワークスペースから上位 N 件を取ってから画面で絞ると、別のワークスペースの run が多いときに
    このワークスペースの run が押し出される。
    """
    teams = await TeamRepository(session).teams_of(uid)
    if workspace is not None:
        if workspace not in teams:
            raise WorkspaceNotFound(workspace)
        teams = {workspace}
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


class WorkspaceNotFound(LookupError):
    """ワークスペースが無い、または呼び出した人に必要な role が無い。

    区別しない（呼び出し側は 404 にして存在を漏らさない）。
    """


async def require_owner(session: AsyncSession, slug: str, uid: str) -> None:
    """uid が slug の owner でなければ `WorkspaceNotFound`（approver は含めない完全一致）。"""
    stmt = select(WorkspaceMember.role).where(
        WorkspaceMember.team == slug, WorkspaceMember.uid == uid
    )
    if (await session.execute(stmt)).scalar_one_or_none() != "owner":
        raise WorkspaceNotFound(slug)


async def require_member(session: AsyncSession, slug: str, uid: str) -> None:
    if slug not in await TeamRepository(session).teams_of(uid):
        raise WorkspaceNotFound(slug)


async def role_of(session: AsyncSession, slug: str, uid: str) -> WorkspaceRole | None:
    """uid の slug での role。メンバーでなければ None。"""
    stmt = select(WorkspaceMember.role).where(
        WorkspaceMember.team == slug, WorkspaceMember.uid == uid
    )
    return (await session.execute(stmt)).scalar_one_or_none()


class NotPermitted(PermissionError):
    """メンバーではあるが、その操作に必要な role が無い（呼び出し側は 403。拒否は監査に残る）。"""


async def record_denial(
    session: AsyncSession,
    audit_factory: async_sessionmaker[AsyncSession] | None,
    *,
    actor: str,
    action: str,
    target: str,
    detail: dict[str, Any],
) -> None:
    """拒否を監査に残す。拒否は例外で返り呼び出し側が rollback するので、`audit_factory` が
    あれば独立したトランザクションで確定させる（`policy_governance._record_denial` と同じ流儀）。
    無ければ同じセッションに書く（テスト用。本番の経路は必ず factory を渡す）。
    """
    if audit_factory is None:
        await AuditRepository(session).record(actor, action, target, "denied", detail)
        return
    async with audit_factory() as audit_session, audit_session.begin():
        await AuditRepository(audit_session).record(actor, action, target, "denied", detail)


async def resolve_github_project(
    session: AsyncSession,
    *,
    github_repo_id: int,
    repo_full_name: str,
    installation_id: int | None,
    actor: str,
    default_branch: str = "main",
) -> Project:
    """GitHub のリポジトリ ID で project を決め（無ければ作る）、名前を奪われた旧行を監査に残す。

    webhook とリポジトリ登録（S55）が同じ規則で project を決める（`resolve_for_webhook`）。
    """
    project, evicted = await ProjectRepository(session).resolve_for_webhook(
        github_repo_id=github_repo_id,
        repo_full_name=repo_full_name,
        installation_id=installation_id,
        default_branch=default_branch,
    )
    for name in evicted:
        await AuditRepository(session).record(
            actor,
            "project.evict_name",
            name,
            "evicted",
            {"reason": "同じ名前の別リポジトリ（ID 違い）が現れた", "new_repo_id": github_repo_id},
        )
    return project


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
    "NotPermitted",
    "WorkspaceNotFound",
    "record_denial",
    "role_of",
    "repository_mismatch",
    "require_member",
    "require_owner",
    "resolve_github_project",
    "visible_project",
    "visible_run",
    "visible_runs",
]
