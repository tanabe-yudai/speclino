"""A2A の議論で得た補足の記録と読み出し（S59）。

補足は正式な回答ではない。質問・Finding・ゲートには触れず、検査の詳細に添えるだけ。
記録できるのはトークンのワークスペースの run だけ（他のワークスペース・存在しないは区別しない）。
呼び出し側は `async with session_factory() as s, s.begin():` で包む。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import McpTokenRecord, Project
from speclino.db.repositories.agent_supplements import AgentSupplementRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.domain.a2a import AgentSupplementView, TokenIdentity

UNKNOWN_SOURCE = "不明なエージェント"


class RunNotInWorkspace(Exception):
    """run が無い、または別のワークスペースの run。"""


async def record(
    session: AsyncSession,
    *,
    identity: TokenIdentity,
    run_id: uuid.UUID,
    requirement_id: str | None,
    question: str,
    content: str,
    a2a_task_id: str,
) -> None:
    run = await ReviewRunRepository(session).get(run_id)
    project = await session.get(Project, run.project_id) if run is not None else None
    if run is None or project is None or project.team != identity.team:
        raise RunNotInWorkspace(str(run_id))
    row = await AgentSupplementRepository(session).add(
        team=identity.team,
        run_id=run_id,
        requirement_id=requirement_id,
        question=question,
        content=content,
        token_id=identity.token_id,
        a2a_task_id=a2a_task_id,
    )
    await AuditRepository(session).record(
        f"a2a:{identity.token_id}",  # persist_audit（a2a.tool）と同じ書き方
        "a2a.supplement",
        str(run_id),
        "recorded",
        {"supplement_id": str(row.id), "requirement_id": requirement_id},
        run_id=run_id,
    )


async def list_for_run(session: AsyncSession, *, run_id: uuid.UUID) -> list[AgentSupplementView]:
    views: list[AgentSupplementView] = []
    for row in await AgentSupplementRepository(session).list_for_run(run_id):
        token = await session.get(McpTokenRecord, row.token_id) if row.token_id else None
        views.append(
            AgentSupplementView(
                id=row.id,
                requirement_id=row.requirement_id,
                question=row.question,
                content=row.content,
                created_at=row.created_at,
                source=token.name if token else UNKNOWN_SOURCE,
            )
        )
    return views
