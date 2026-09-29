"""リポジトリの登録 API（S55）。判断は `services/repo_registration.py`。

- 一覧はワークスペースのメンバーなら見える。登録の開始は owner だけ（それ以外は 404）
- コールバックは SPA の `/github/callback` が、GitHub から戻った code と state をログイン済みの
  ID トークン付きで渡す
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.adapters.github.user_oauth import GitHubUserOAuth
from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser
from speclino.services import repo_registration
from speclino.services.repo_registration import RegistrationError
from speclino.services.workspaces import WorkspaceNotFound

router = APIRouter(tags=["repositories"], responses=error_responses(401))

CALLBACK_PATH = "/github/callback"


class AuthorizeRequest(BaseModel):
    repo_full_name: str = Field(min_length=3, max_length=200)


class CallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=1, max_length=512)


def _redirect_uri(state: AppState) -> str:
    return state.settings.public_url.rstrip("/") + CALLBACK_PATH


def _oauth(state: AppState) -> GitHubUserOAuth:
    if state.github_user_oauth is None:
        raise api_error(503, "github_oauth_unconfigured", "GitHub のユーザー認可が未設定")
    return state.github_user_oauth


@router.get("/api/workspaces/{slug}/repositories", responses=error_responses(404))
async def list_repositories(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            repos = await repo_registration.list_repositories(s, slug=slug, uid=user.uid)
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    return [
        {
            "repo_full_name": r.repo_full_name,
            "github_repo_id": r.github_repo_id,
            "default_branch": r.default_branch,
        }
        for r in repos
    ]


@router.post(
    "/api/workspaces/{slug}/repositories/authorize", responses=error_responses(400, 404, 503)
)
async def authorize(
    slug: str,
    body: AuthorizeRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, str]:
    oauth = _oauth(state)
    try:
        async with state.session_factory() as s, s.begin():
            url = await repo_registration.start(
                s,
                slug=slug,
                uid=user.uid,
                repo_full_name=body.repo_full_name,
                client_id=oauth.client_id,
                redirect_uri=_redirect_uri(state),
            )
    except WorkspaceNotFound as e:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from e
    except RegistrationError as e:
        raise api_error(400, e.code, str(e)) from e
    return {"authorize_url": url}


@router.post("/api/github/callback", responses=error_responses(400, 503))
async def callback(
    body: CallbackRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, str]:
    oauth = _oauth(state)
    try:
        result = await repo_registration.complete(
            state.session_factory,
            oauth,
            uid=user.uid,
            code=body.code,
            state=body.state,
            redirect_uri=_redirect_uri(state),
        )
    except WorkspaceNotFound as e:
        # 認可の間に owner でなくなった
        raise api_error(400, "invalid_state", "このワークスペースの owner ではない") from e
    except RegistrationError as e:
        raise api_error(400, e.code, str(e)) from e
    return {"slug": result.slug, "repo_full_name": result.repo_full_name, "status": result.status}
