"""A2A を FastAPI にマウントする（Agent Card、`/a2a` の JSON-RPC、トークン認証、DB のタスク）。"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from a2a.server.context import ServerCallContext
from a2a.server.models import TaskModel, create_task_model
from a2a.server.owner_resolver import OwnerResolver
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import DatabaseTaskStore, TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from speclino.a2a_server.auth import A2ABearerAuth, WorkspaceContextBuilder, owner_of
from speclino.a2a_server.card import A2A_PATH, build_agent_card
from speclino.a2a_server.executor import SkillHandler, SpecLinoExecutor
from speclino.a2a_server.handlers import build_handlers
from speclino.api.deps import AppState
from speclino.domain.a2a import SkillId
from speclino.settings import Settings

A2A_TASKS_TABLE = "a2a_tasks"


@cache
def _a2a_task_model() -> type[TaskModel]:
    """`a2a_tasks` の ORM クラス。SDK の metadata に 1 回しか定義できないのでプロセスで共有する。"""
    model: type[TaskModel] = create_task_model(A2A_TASKS_TABLE)
    return model


def a2a_task_store(engine: AsyncEngine, owner_resolver: OwnerResolver) -> DatabaseTaskStore:
    """`a2a_tasks` を読み書きする DatabaseTaskStore。

    table_name を渡すと作るたびに ORM クラスを定義し直し、同じプロセスの 2 個目で落ちる
    （app を作り直すテストなど）。既定のまま作り、共有の a2a_tasks のクラスに差し替える。
    """
    store = DatabaseTaskStore(engine, create_table=False, owner_resolver=owner_resolver)
    store.task_model = _a2a_task_model()
    return store


class LazyTaskStore(TaskStore):
    """最初の呼出しで state.engine から DatabaseTaskStore を作り、以後は委譲する。

    `create_app` の時点では state がまだ無い（lifespan で入る）ため、リクエストのときに引く。
    持ち主（owner）はトークンのワークスペースで、他のワークスペースのタスクは「無い」と同じ。
    """

    def __init__(self, get_state: Callable[[], AppState]) -> None:
        self.get_state = get_state
        self._inner: DatabaseTaskStore | None = None

    def _store(self) -> DatabaseTaskStore:
        if self._inner is None:
            self._inner = a2a_task_store(self.get_state().engine, owner_of)
        return self._inner

    async def save(self, task: Task, context: ServerCallContext) -> None:
        await self._store().save(task, context)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        return await self._store().get(task_id, context)

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        return await self._store().list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._store().delete(task_id, context)


class _Handlers:
    """スキルの処理。state.a2a_handlers があればそれ（テスト）、無ければ state から組み立てる。

    組み立てた結果は state ごとに 1 回だけ作って使い回す。組み立てに失敗したら覚えない。
    """

    def __init__(self, get_state: Callable[[], AppState]) -> None:
        self.get_state = get_state
        self._built: tuple[AppState, dict[SkillId, SkillHandler]] | None = None

    def __call__(self) -> dict[SkillId, SkillHandler]:
        state = self.get_state()
        if state.a2a_handlers is not None:
            return state.a2a_handlers
        if self._built is None or self._built[0] is not state:
            self._built = (state, build_handlers(state))
        return self._built[1]


def mount_a2a(app: FastAPI, settings: Settings, get_state: Callable[[], AppState]) -> None:
    """静的配信の mount より前に呼ぶ（"/" の mount に `/a2a` を取られないように）。"""
    card = build_agent_card(settings.public_url.rstrip("/"))
    executor = SpecLinoExecutor(_Handlers(get_state), lambda: get_state().session_factory)
    handler = DefaultRequestHandler(executor, LazyTaskStore(get_state), card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler,
            A2A_PATH,
            context_builder=WorkspaceContextBuilder(),
            # 0.3 系の SDK を使う仲介（MCP 経由の Claude Code など）も話せるようにする
            enable_v0_3_compat=True,
        ),
    )
    app.add_middleware(A2ABearerAuth, get_state=get_state, paths=(A2A_PATH,))
