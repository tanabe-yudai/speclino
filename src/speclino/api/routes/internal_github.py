"""worker ロールの内部エンドポイント: 承認者のログイン名 → GitHub ユーザー ID（S61）。

web は GitHub App の秘密鍵を持たず、認証なしの公開 API は Cloud Run の共有 IP でレート制限に
当たるので、App の認証を持つ worker が引く。web から OIDC で呼ばれ、呼び出せるのは Cloud Run の
IAM で許可した SA（tasks と web）だけ（`internal_mcp.py` と同じ）。
"""

from __future__ import annotations

import logging

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
