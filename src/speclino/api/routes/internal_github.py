"""worker ロールの内部エンドポイント: 承認者のログイン名 → GitHub ユーザー ID（S61）。

web は GitHub App の秘密鍵を持たず、認証なしの公開 API は Cloud Run の共有 IP でレート制限に
当たるので、App の認証を持つ worker が引く。web から OIDC で呼ばれ、呼び出せるのは Cloud Run の
IAM で許可した SA（tasks と web）だけ（`internal_mcp.py` と同じ）。

`/repo` は知識の会話の調査（読むだけ）。web はワークスペースに登録されたリポジトリと、木から
引いた 40 桁の SHA だけを渡す。ここでも値の形を確かめる。
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import asdict
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.ports.github_users import GitHubUserLookupError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/github", tags=["internal"])


class UserIdRequest(BaseModel):
    login: str = Field(min_length=1, max_length=39)


@router.post("/user-id", responses=error_responses(503))
async def github_user_id(
    body: UserIdRequest, state: AppState = Depends(get_state)
) -> dict[str, int | None]:
    try:
        return {"id": await state.github_users.user_id(body.login)}
    except ValueError as e:
        raise api_error(422, "invalid_github_login", str(e)) from e
    except GitHubUserLookupError as e:
        logger.warning("GitHub のユーザー ID を引けない: login=%s reason=%s", body.login, e)
        raise api_error(503, "github_lookup_failed", str(e)) from e


_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,255}")


class RepoRequest(BaseModel):
    op: Literal["id", "head", "tree", "blob"]
    repo: str = Field(min_length=3, max_length=201)
    ref: str = Field(default="", max_length=255)


@router.post("/repo", responses=error_responses(422, 503))
async def github_repo(body: RepoRequest, state: AppState = Depends(get_state)) -> dict[str, Any]:
    reader = state.repo_reader
    if reader is None:
        raise api_error(503, "repo_reader_unavailable", "リポジトリを読めない構成")
    if not _REPO_RE.fullmatch(body.repo) or ".." in body.repo:
        raise api_error(422, "invalid_repo", "リポジトリ名の形が正しくない")
    needs_sha = body.op in ("tree", "blob")
    if (needs_sha and not _SHA_RE.fullmatch(body.ref)) or (
        body.op == "head" and not _BRANCH_RE.fullmatch(body.ref)
    ):
        raise api_error(422, "invalid_ref", "ref の形が正しくない")
    try:
        if body.op == "id":
            return {"id": await reader.repository_id(body.repo)}
        if body.op == "head":
            return {"sha": await reader.branch_head(body.repo, body.ref)}
        if body.op == "tree":
            tree = await reader.tree(body.repo, body.ref)
            return {"entries": [asdict(e) for e in tree.entries], "truncated": tree.truncated}
        content = await reader.blob(body.repo, body.ref)
        return {"content_b64": base64.b64encode(content).decode()}
    except (httpx.HTTPError, ValueError, LookupError) as e:
        logger.warning("リポジトリを読めない: op=%s reason=%s", body.op, type(e).__name__)
        raise api_error(503, "repo_read_failed", "リポジトリを読めない") from e
