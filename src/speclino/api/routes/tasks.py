"""worker ロールの内部エンドポイント（Cloud Tasks から OIDC で呼ばれる。ローカルは inprocess）。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from speclino.api.deps import AppState, get_state

router = APIRouter(prefix="/internal/tasks", tags=["internal"])


class RunTask(BaseModel):
    run_id: uuid.UUID


@router.post("/run")
async def run_task(
    body: RunTask, request: Request, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    # OIDC の検証は Cloud Run の IAM（worker は tasks SA のみ invoker）に委ねる（S26）。
    # 再配信・二重実行は service.execute の try_acquire が排除する。
    await state.service.execute(body.run_id)
    return {"status": "done", "run_id": str(body.run_id)}
