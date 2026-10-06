"""知識レジストリへの切り替えの進み具合（`speclino cutover-status`、#114）。読み取りだけ。

ワークスペースに登録された各プロジェクトについて、次の 3 つがそろったかを見る
（docs/runbooks/knowledge-registry-cutover.md の手順の確認）:

1. 今の bundle に、このプロジェクトに当たる承認済みの仕様がある
2. 失効していない sync トークンがある
3. 最新の完了した検査の `KNOWLEDGE-LOCK-001` が VERIFIED_IN_SCOPE（lock をコミットして検査し直した）

bundle は作らない（`current_bundle` を読むだけ）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, SyncTokenRecord
from speclino.db.repositories import FindingRepository, ReviewRunRepository
from speclino.domain.finding import Verdict
from speclino.domain.knowledge_view import CutoverProjectStatus
from speclino.services import knowledge_registry as kr
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID


async def _lock_verdict(
    session: AsyncSession, project: Project
) -> tuple[str | None, Verdict | None]:
    run = await ReviewRunRepository(session).latest_completed(project.id)
    if run is None:
        return None, None
    for row in await FindingRepository(session).list_for_run(run.id):
        if row.requirement_id == LOCK_REQUIREMENT_ID:
            verdict: Verdict = row.verdict
            return str(run.id), verdict
    return str(run.id), None


async def _active_tokens(session: AsyncSession, project: Project) -> int:
    stmt = select(func.count()).where(
        SyncTokenRecord.project_id == project.id,
        SyncTokenRecord.team == project.team,
        SyncTokenRecord.revoked_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one())


async def cutover_status(
    session: AsyncSession, workspace: str | None = None
) -> list[CutoverProjectStatus]:
    """ワークスペース（省略で全部）に登録されたプロジェクトごとの状態。ワークスペース・リポジトリ名順。"""
    stmt = select(Project).where(Project.team.is_not(None))
    if workspace is not None:
        stmt = stmt.where(Project.team == workspace)
    projects = list(
        (await session.execute(stmt.order_by(Project.team, Project.repo_full_name))).scalars()
    )
    bundles = {}
    out: list[CutoverProjectStatus] = []
    for project in projects:
        team = project.team
        assert team is not None
        if team not in bundles:
            bundles[team] = await kr.current_bundle(session, team)
        bundle = bundles[team]
        specs: list[str] = []
        problem: str | None = None
        if bundle is None:
            problem = "bundle がまだ無い（承認か検査で作られる）"
        else:
            try:
                resolved = await kr.resolve_for_project(session, bundle, str(project.id))
                specs = sorted(r.name for r in resolved.specs)
            except (ValueError, LookupError) as e:
                problem = str(e)
        run_id, verdict = await _lock_verdict(session, project)
        tokens = await _active_tokens(session, project)
        out.append(
            CutoverProjectStatus(
                workspace=team,
                project_id=str(project.id),
                repo_full_name=project.repo_full_name,
                bundle_generation=bundle.generation if bundle is not None else None,
                approved_specs=specs,
                bundle_problem=problem,
                active_sync_tokens=tokens,
                latest_run_id=run_id,
                lock_verdict=verdict,
                ready=bool(specs) and tokens > 0 and verdict == "VERIFIED_IN_SCOPE",
            )
        )
    return out


__all__ = ["cutover_status"]
