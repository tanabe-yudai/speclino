"""知識レジストリ（S62）の API。判断は services/knowledge_registry.py。

メンバー以外・別のワークスペースの artifact は 404（存在を漏らさない）。下書きの作成・保存は
メンバー、承認は owner / approver（それ以外は 403 で、拒否は監査に残る）。
リクエストに入れられるのは人が書く入力だけ（kind・名前・内容・見た番号と hash）。改訂番号・
状態・hash・作者・承認者・ワークスペースはサーバが決める（`extra="forbid"` で拒否する）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.knowledge import ArtifactContent, ArtifactKind
from speclino.domain.knowledge_view import ApproveResult
from speclino.domain.workspace import AuthUser
from speclino.services import knowledge_registry as kr
from speclino.services.policy_governance import DraftConflict
from speclino.services.workspaces import WorkspaceNotFound, require_member

router = APIRouter(prefix="/api/w/{slug}", tags=["knowledge"], responses=error_responses(401, 404))


class CreateArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    content: ArtifactContent


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 保存する人が前提にした改訂（下書きがあればその番号、無ければ今の active の番号）
    expected_revision: int = Field(ge=1)
    content: ArtifactContent


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_hash: str = Field(min_length=1, max_length=200)


def _not_found(e: Exception) -> Exception:
    if isinstance(e, WorkspaceNotFound):
        return api_error(404, "workspace_not_found", "ワークスペースが無い")
    return api_error(404, "artifact_not_found", "知識が無い")


@router.get("/artifacts")
async def list_artifacts(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            rows = await kr.list_artifacts(s, slug)
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    return [r.model_dump(mode="json") for r in rows]


@router.post("/artifacts", responses=error_responses(409, 422))
async def create_artifact(
    slug: str,
    body: CreateArtifactRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            rev = await kr.create_draft(
                s,
                workspace=slug,
                kind=body.kind,
                name=body.name,
                content=body.content,
                author=user.uid,
            )
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    except kr.ArtifactExists as e:
        raise api_error(409, "artifact_exists", str(e)) from e
    except kr.InvalidArtifact as e:
        raise api_error(422, "invalid_artifact", str(e)) from e
    return rev.model_dump(mode="json")


@router.get("/artifacts/{artifact_id}/draft")
async def get_draft(
    slug: str,
    artifact_id: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            await kr.require_artifact(s, slug, artifact_id)
            draft = await kr.current_draft(s, artifact_id)
    except (WorkspaceNotFound, kr.ArtifactNotFound) as e:
        raise _not_found(e) from e
    if draft is None:
        raise api_error(404, "draft_not_found", "下書きが無い")
    return draft.model_dump(mode="json")


@router.put("/artifacts/{artifact_id}/draft", responses=error_responses(409, 422))
async def put_draft(
    slug: str,
    artifact_id: str,
    body: DraftRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            await kr.require_artifact(s, slug, artifact_id)
            rev = await kr.update_draft(
                s,
                artifact_id=artifact_id,
                expected_revision=body.expected_revision,
                content=body.content,
                author=user.uid,
            )
    except (WorkspaceNotFound, kr.ArtifactNotFound) as e:
        raise _not_found(e) from e
    except kr.InvalidArtifact as e:
        raise api_error(422, "invalid_artifact", str(e)) from e
    except DraftConflict as e:
        raise api_error(409, "draft_changed", "下書きが変わった。読み直してから保存する") from e
    return rev.model_dump(mode="json")


@router.post("/artifacts/{artifact_id}/approve", responses=error_responses(403, 409, 422))
async def approve(
    slug: str,
    artifact_id: str,
    body: ApproveRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            await kr.require_artifact(s, slug, artifact_id)
            bundle = await kr.approve(
                s,
                artifact_id=artifact_id,
                expected_revision=body.expected_revision,
                expected_hash=body.expected_hash,
                approver=user.uid,
                audit_factory=state.session_factory,
            )
            revision = await kr.revision_at(s, artifact_id, body.expected_revision)
    except (WorkspaceNotFound, kr.ArtifactNotFound) as e:
        raise _not_found(e) from e
    except PermissionError as e:
        raise api_error(403, "not_an_approver", "承認できるのは owner / approver だけ") from e
    except DraftConflict as e:
        raise api_error(409, "draft_changed", "下書きが変わった。読み直してから承認する") from e
    except kr.BundleConflict as e:
        raise api_error(409, "bundle_conflict", str(e)) from e
    except kr.SpecNotCompilable as e:
        raise api_error(422, "spec_not_compilable", str(e)) from e
    return ApproveResult(revision=revision, bundle=bundle).model_dump(mode="json")


@router.get("/artifacts/{artifact_id}/revisions")
async def list_revisions(
    slug: str,
    artifact_id: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            await kr.require_artifact(s, slug, artifact_id)
            rows = await kr.list_revisions(s, artifact_id)
    except (WorkspaceNotFound, kr.ArtifactNotFound) as e:
        raise _not_found(e) from e
    return [r.model_dump(mode="json") for r in rows]


@router.get("/bundles/current")
async def current_bundle(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            bundle = await kr.current_bundle(s, slug)
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    if bundle is None:
        raise api_error(404, "bundle_not_found", "まだ bundle が無い")
    return bundle.model_dump(mode="json")
