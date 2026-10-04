"""run の状態・ゲート結果・Finding の読み出し（MCP の verification_get と A2A の議論役で共有）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project
from speclino.db.repositories import FindingRepository, ReviewRunRepository

# MCP に返す Finding の射影（full=False）
FINDING_FIELDS = ("requirement_id", "verdict", "category", "severity", "message", "next_steps")


async def run_view(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    workspace: str,
    *,
    full: bool = False,
) -> dict[str, Any] | None:
    """workspace の run の状態。無い・他のワークスペースの run は None（存在を漏らさない）。

    full=True は Finding の payload 全体（id・spec_refs・反例を含む）を返す。
    """
    async with session_factory() as session:
        run = await ReviewRunRepository(session).get(run_id)
        project = await session.get(Project, run.project_id) if run is not None else None
        if run is None or project is None or project.team != workspace:
            return None
        findings = await FindingRepository(session).list_for_run(run_id)
    manifest = run.manifest or {}
    return {
        "run_id": str(run_id),
        "exec_state": run.exec_state,
        "gate_status": run.gate_status,
        "gate": manifest.get("gate"),
        "semantic_diff": manifest.get("semantic_diff"),
        "findings": [
            dict(f.payload)
            if full
            else {k: f.payload.get(k) for k in FINDING_FIELDS if k in f.payload}
            for f in findings
        ],
    }
