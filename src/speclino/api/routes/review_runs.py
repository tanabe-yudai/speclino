"""確認画面用 API（読取り専用）と手動の検証開始。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.models import Project, ReviewRun
from speclino.db.repositories import FindingRepository, ReviewRunRepository, RunEventRepository
from speclino.services.demo_rerun import RerunNotFound, demo_rerun

router = APIRouter(
    prefix="/api/review-runs", tags=["review-runs"], responses=error_responses(401, 403)
)


class StartRunRequest(BaseModel):
    repo_full_name: str = Field(min_length=3)
    head_sha: str = Field(min_length=7)
    base_sha: str | None = None
    pr_number: int | None = None


def _run_view(run: ReviewRun, repo: str | None = None) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "repo_full_name": repo,
        "pr_number": run.pr_number,
        "head_sha": run.head_sha,
        "base_sha": run.base_sha,
        "trigger": run.trigger,
        "exec_state": run.exec_state,
        "gate_status": run.gate_status,
        "check_run_id": run.check_run_id,
        "artifacts_uri": run.artifacts_uri,
        "manifest": run.manifest,
        "budget": run.budget,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("", status_code=202)
async def start_run(
    body: StartRunRequest,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    run_id = await state.service.create_run(
        repo_full_name=body.repo_full_name,
        head_sha=body.head_sha,
        base_sha=body.base_sha,
        pr_number=body.pr_number,
        trigger=f"manual:{user}",
    )
    return {"run_id": str(run_id)}


@router.get("")
async def list_runs(
    limit: int = 50,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    async with state.session_factory() as session:
        stmt = (
            select(ReviewRun, Project.repo_full_name)
            .join(Project, Project.id == ReviewRun.project_id)
            .order_by(ReviewRun.created_at.desc())
            .limit(min(limit, 200))
        )
        rows = (await session.execute(stmt)).all()
        return [_run_view(run, repo) for run, repo in rows]


@router.get("/{run_id}", responses=error_responses(404))
async def get_run(
    run_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    async with state.session_factory() as session:
        run = await ReviewRunRepository(session).get(run_id)
        if run is None:
            raise api_error(404, "run_not_found", "run が無い")
        project = await session.get(Project, run.project_id)
        findings = await FindingRepository(session).list_for_run(run_id)
        return {
            **_run_view(run, project.repo_full_name if project else None),
            "findings": [f.payload for f in findings],
        }


@router.post("/{run_id}/rerun", status_code=202, responses=error_responses(404, 429))
async def rerun_run(
    run_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """デモ再実行（S39）: 同じ head/base で run を作り直す。日次の回数上限と費用枠で守る。"""
    try:
        outcome = await demo_rerun(
            state.session_factory,
            state.service,
            state.service.cost_guard,
            run_id,
            user,
            state.settings.demo_rerun_limit_per_day,
        )
    except RerunNotFound as e:
        raise api_error(404, "run_not_found", "run が無い") from e
    if outcome.run_id is None:
        raise api_error(429, "rerun_limit", outcome.reason)
    return {
        "run_id": str(outcome.run_id),
        "used_today": outcome.used_today,
        "limit": outcome.limit,
        "remaining": outcome.remaining,
        "cost_ok": outcome.cost_ok,
        "cost_note": outcome.cost_note,
    }


@router.get("/{run_id}/events")
async def get_events(
    run_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    async with state.session_factory() as session:
        events = await RunEventRepository(session).list_for_run(run_id)
        return [
            {"seq": e.seq, "ts": e.ts.isoformat(), "type": e.type, "payload": e.payload}
            for e in events
        ]


@router.get("/{run_id}/artifacts/{name}", responses=error_responses(400, 404))
async def get_artifact(
    run_id: uuid.UUID,
    name: str,
    state: AppState = Depends(get_state),
    user: str = Depends(require_user),
) -> Any:
    from fastapi.responses import Response

    if "/" in name or name.startswith("."):
        raise api_error(400, "invalid_name", "不正な名前")
    try:
        data = await state.blobs.get(f"runs/{run_id}/{name}")
    except (FileNotFoundError, OSError) as e:
        raise api_error(404, "artifact_not_found", "成果物が無い") from e
    media = "application/json" if name.endswith(".json") else "text/markdown; charset=utf-8"
    return Response(content=data, media_type=media)
