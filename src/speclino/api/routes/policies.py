"""チーム規約の画面（S43）の API。判断は services/policy_governance.py と team_policies.py。

メンバー以外は 404。下書きの保存はメンバー、承認は owner / approver
（それ以外は 403 で、拒否は監査に残る）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.knowledge_chat import NoteSource
from speclino.domain.policy import PolicyPackage
from speclino.domain.workspace import AuthUser
from speclino.services import policy_governance as gov
from speclino.services.knowledge_registry import BundleConflict
from speclino.services.team_policies import load_policies
from speclino.services.workspaces import WorkspaceNotFound, require_member

router = APIRouter(
    prefix="/api/workspaces/{slug}/policies", tags=["policies"], responses=error_responses(401, 404)
)

HISTORY_LIMIT = 50


class DraftRequest(BaseModel):
    package: PolicyPackage
    note: str = Field(default="", max_length=500)
    # 本人の申告（S73: 会話の要約から入れたか）。監査に残すだけ
    note_source: NoteSource = "manual"
    # 保存する人が前提にした下書き（無ければ両方 null）。今の下書きと違えば 409
    expected_revision: int | None = Field(default=None, ge=1)
    expected_hash: str | None = Field(default=None, max_length=200)


class ApproveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_hash: str = Field(min_length=1, max_length=200)
    # 承認者が見た説明（note）のハッシュ（S73、任意）。違えば 409
    expected_note_hash: str | None = Field(default=None, max_length=200)


def _not_found() -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


@router.get("")
async def get_policies(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            view = await load_policies(s, state.blobs, slug, user.uid)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return view.model_dump(mode="json")


@router.put("/draft", responses=error_responses(409, 422))
async def put_draft(
    slug: str,
    body: DraftRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            draft = await gov.save_draft(
                s,
                team=slug,
                package=body.package,
                author=user.uid,
                note=body.note,
                note_source=body.note_source,
                expected_revision=body.expected_revision,
                expected_hash=body.expected_hash,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except gov.InvalidPackage as e:
        raise api_error(422, "invalid_policy", str(e)) from e
    except gov.DraftConflict as e:
        raise api_error(409, "draft_changed", "下書きが変わった。読み直してから保存する") from e
    return draft.model_dump(mode="json")


@router.post("/draft/approve", responses=error_responses(403, 409))
async def approve_draft(
    slug: str,
    body: ApproveRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            active = await gov.approve_draft(
                s,
                team=slug,
                expected_revision=body.expected_revision,
                expected_hash=body.expected_hash,
                approver=user.uid,
                audit_factory=state.session_factory,
                expected_note_hash=body.expected_note_hash,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except PermissionError as e:
        raise api_error(403, "not_an_approver", "承認できるのは owner / approver だけ") from e
    except gov.DraftConflict as e:
        raise api_error(409, "draft_changed", "下書きが変わった。読み直してから承認する") from e
    except BundleConflict as e:
        # 規約の有効化は知識の bundle を作り直す（S62）。作れなければ承認ごと取り消す
        raise api_error(409, "bundle_conflict", str(e)) from e
    return active.model_dump(mode="json")


@router.get("/revisions")
async def list_revisions(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            rows = await PolicyRevisionRepository(s).list_summaries(slug, HISTORY_LIMIT)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return [r.model_dump(mode="json") for r in rows]


@router.get("/audit")
async def list_audit(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            rows = await AuditRepository(s).list_for_team(
                slug, ("policy.draft", "policy.approve"), HISTORY_LIMIT
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return [r.model_dump(mode="json") for r in rows]
