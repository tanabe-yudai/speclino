"""人の確定（S70）の API。判断は services/attestations.py。

確定できるのは owner / approver（member は 403、拒否は監査に残る）。対応表・免除・規約の変更
（KNOWLEDGE-BINDINGS-001）の確定は、PR の作成者と照合できる PR コメントだけで受け付け、ここでは
403（`bindings_attest_requires_pr_comment`、裁定 35）。メンバーでない・別の
ワークスペースは 404（存在を漏らさない）。`run_id` は必須で、その run がこのワークスペースの
プロジェクトのものであり、(指紋, hash) がその run の確定できる指摘であるときだけ受け付ける
（それ以外は 404。#114）。確定はその run のプロジェクトに属し、その PR の最新の head で再検査の
run を作る（PR コメントの承認と同じく、確定は次の run のゲートに効く）。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.assessment import Disposition
from speclino.domain.workspace import AuthUser
from speclino.services import attestations as svc
from speclino.services.workspaces import WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}", tags=["attestations"], responses=error_responses(401, 404)
)


class AttestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=1, max_length=300, pattern=r"^\S+$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    disposition: Disposition
    # 確定した画面の run（必須）。その run の指摘だけを確定でき、その PR の最新の head で再検査する
    run_id: uuid.UUID


@router.post("/attestations", responses=error_responses(403, 422))
async def create_attestation(
    slug: str,
    body: AttestRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            saved, target = await svc.attest_as_member(
                s,
                workspace=slug,
                uid=user.uid,
                fingerprint=body.fingerprint,
                content_hash=body.content_hash,
                disposition=body.disposition,
                audit_factory=state.session_factory,
                run_id=body.run_id,
            )
            project, latest = target
            recheck = await state.service.create_run_in(
                s,
                project=project,
                head_sha=latest.head_sha,
                base_sha=latest.base_sha,
                pr_number=latest.pr_number,
                trigger="attestation",
            )
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    except svc.AttestTargetNotFound as e:
        raise api_error(404, "attestation_target_not_found", "確定できる指摘が無い") from e
    except svc.BindingsAttestRequiresPrComment as e:
        raise api_error(403, "bindings_attest_requires_pr_comment", str(e)) from e
    except PermissionError as e:
        raise api_error(403, "not_an_approver", "確定できるのは owner / approver だけ") from e
    except LookupError as e:
        raise api_error(404, "run_not_found", "run が無い") from e
    await state.queue.enqueue(recheck)
    return {
        "attestation": saved.model_dump(mode="json"),
        "recheck_run_id": str(recheck),
    }
