"""A2A の認証: ワークスペースの MCP トークンで `/a2a` を守り、身元を呼出しの文脈に載せる。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.routes import DefaultServerCallContextBuilder
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from speclino.api.deps import AppState
from speclino.domain.a2a import TokenIdentity
from speclino.services import mcp_tokens

# A2ABearerAuth が scope["state"] に、WorkspaceContextBuilder が ServerCallContext.state に置く鍵
IDENTITY_STATE_KEY = "a2a_identity"


class A2ABearerAuth:
    """`paths` の完全一致だけをトークンで守る ASGI ミドルウェア（Agent Card は公開のまま）。

    無い・不正・失効済みは区別せず 401。有効なら `TokenIdentity` を `scope["state"]` に載せる。
    """

    def __init__(
        self, app: ASGIApp, get_state: Callable[[], AppState], paths: Sequence[str] = ("/a2a",)
    ) -> None:
        self.app = app
        self.get_state = get_state
        self.paths = tuple(paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] in self.paths:
            auth = Request(scope, receive).headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            identity = await self._authenticate(token) if token else None
            if identity is None:
                response = JSONResponse(
                    {"error": {"code": "unauthenticated", "message": "トークンが必要"}},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})[IDENTITY_STATE_KEY] = identity
        await self.app(scope, receive, send)

    async def _authenticate(self, token: str) -> TokenIdentity | None:
        async with self.get_state().session_factory() as session, session.begin():
            return await mcp_tokens.authenticate_identity(session, token)


class _WorkspaceUser(User):
    def __init__(self, team: str) -> None:
        self._team = team

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._team


class WorkspaceContextBuilder(DefaultServerCallContextBuilder):
    """リクエストに載ったトークンの身元を ServerCallContext に移す。

    既定の builder を継承する（自前の builder だと SDK のバージョン確認が通らない）。
    """

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        ctx.state[IDENTITY_STATE_KEY] = _identity(request)
        return ctx

    def build_user(self, request: Request) -> User:
        return _WorkspaceUser(_identity(request).team)


def _identity(request: Request) -> TokenIdentity:
    identity = getattr(request.state, IDENTITY_STATE_KEY, None)
    if not isinstance(identity, TokenIdentity):
        raise PermissionError("A2A トークンの身元が無い")
    return identity


def owner_of(ctx: ServerCallContext) -> str:
    """タスクの持ち主（ワークスペース）。DatabaseTaskStore の owner_resolver。"""
    identity = ctx.state.get(IDENTITY_STATE_KEY)
    if not isinstance(identity, TokenIdentity):
        raise PermissionError("A2A トークンの身元が無い")
    return identity.team
