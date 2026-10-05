"""仕様エディタのプレビュー API（S66）。判断は services/spec_preview.py。

メンバーだけが使える（別のワークスペース・存在しないワークスペースは 404）。保存はしない。
本文の大きさの上限は `compile_spec` が診断で返すが、その手前で body を 512KB に制限する
（JSON の解析より前に、ストリームを読みながら打ち切る）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.spec_ir import SpecIR
from speclino.domain.workspace import AuthUser
from speclino.services.spec_preview import preview
from speclino.services.workspaces import WorkspaceNotFound, require_member

MAX_BODY_BYTES = 512 * 1024

router = APIRouter(
    prefix="/api/workspaces/{slug}/specs", tags=["knowledge"], responses=error_responses(401, 404)
)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str
    domain: DomainSpec | None = None
    legacy_ir: list[SpecIR] = Field(default_factory=list)


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise api_error(413, "payload_too_large", "本文が大きすぎる（512KB まで）")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise api_error(413, "payload_too_large", "本文が大きすぎる（512KB まで）")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/preview",
    responses=error_responses(413, 422),
    # body は 512KB の上限を確かめてから自前で読む（PreviewRequest: markdown・domain?・legacy_ir?）
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
async def preview_spec(
    slug: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    raw = await _read_limited(request)
    try:
        body = PreviewRequest.model_validate_json(raw)
    except ValidationError as e:
        raise api_error(422, "validation_error", str(e.errors(include_url=False))) from e
    result = preview(body.markdown, domain=body.domain, legacy_ir=body.legacy_ir)
    return result.model_dump(mode="json")
