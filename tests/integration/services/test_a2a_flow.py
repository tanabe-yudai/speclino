"""A2A（S59）: Agent Card の公開、トークン認証、入力待ちの往復、DB のタスク、監査の保存。

スキルの中身は偽の処理（`_echo`）に差し替え、入口の骨組みだけを確かめる。"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.helpers import get_artifact_text, get_message_text, new_text_part
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, Task, TaskState
from a2a.utils.errors import A2AError, TaskNotFoundError
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.a2a_server.card import build_agent_card
from speclino.a2a_server.executor import SkillCall, SkillHandler
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.agent.gateway import ToolCall
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.domain.a2a import SkillId
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.test_mcp_flow import _issue

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]

A2A_TEAM = "e2e-a2a"


async def _echo(call: SkillCall) -> None:
    """1 回目は質問して入力待ち、2 回目は答えを成果物にして完了する。"""
    if call.state is None:
        await call.updater.requires_input(
            call.updater.new_agent_message(
                [new_text_part("behavior は？")], metadata={"speclino": {"round": 1}}
            )
        )
        return
    await call.updater.add_artifact(
        [new_text_part(f"{call.state['round']}:{call.reply}")], name="result"
    )
    await call.updater.complete()


@asynccontextmanager
async def _app(
    engine: AsyncEngine,
    tmp_path: Path,
    handlers: dict[SkillId, SkillHandler],
    team: str = A2A_TEAM,
) -> AsyncIterator[tuple[httpx.AsyncClient, Client]]:
    """ワークスペースとトークンを作り、A2A を有効にしたアプリとクライアントを返す。"""
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO teams (team, approvers) VALUES (:t, '[]') ON CONFLICT DO NOTHING"
            ),
            {"t": team},
        )
    token = await _issue(engine, team)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", code_host_kind="local", a2a_enabled=True)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service, a2a_handlers=handlers)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http,
    ):
        config = ClientConfig(streaming=False, httpx_client=http)
        yield http, ClientFactory(config).create(build_agent_card("http://localhost"))


async def _send(
    client: Client, text: str, task_id: str | None = None, context_id: str | None = None
) -> Task:
    """メッセージを送り、最後の応答のタスクを返す。"""
    message = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=[new_text_part(text)])
    if task_id:
        message.task_id = task_id
    if context_id:
        message.context_id = context_id
    last = None
    async for event in client.send_message(SendMessageRequest(message=message)):
        last = event
    assert last is not None and last.HasField("task"), last
    return last.task


async def _audit_actions(engine: AsyncEngine, task_id: str) -> list[str]:
    """そのタスクで audit_events に残った行の action。"""
    async with engine.connect() as conn:
        rows = await conn.execute(
            sql_text("SELECT action FROM audit_events WHERE detail->>'a2a_task_id' = :t"),
            {"t": task_id},
        )
        return [r[0] for r in rows]


async def test_card_is_public_and_rpc_requires_token(engine: AsyncEngine, tmp_path: Path) -> None:
    async with _app(engine, tmp_path, handlers={"spec_review": _echo}) as (http, _):
        card = await http.get("/.well-known/agent-card.json", headers={"Authorization": ""})
        assert card.status_code == 200
        assert {s["id"] for s in card.json()["skills"]} == {
            "spec_review",
            "requirement_consult",
            "run_clarification",
        }
        denied = await http.post("/a2a", json={}, headers={"Authorization": "Bearer slmcp_bad"})
        assert denied.status_code == 401


async def test_input_required_round_trip_survives_on_db(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    async with _app(engine, tmp_path, handlers={"spec_review": _echo}) as (_, client):
        first = await _send(client, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    # 別のアプリ（別インスタンス相当）でも DB のタスクから続きが読める。
    # 2 回目の判定（history[0] と今回のメッセージの ID の比較）が効かなければ state が None に
    # なり、もう一度質問して INPUT_REQUIRED のままになる
    async with _app(engine, tmp_path, handlers={"spec_review": _echo}) as (_, client):
        done = await _send(client, "orders.cancel", task_id=first.id, context_id=first.context_id)
        assert done.status.state == TaskState.TASK_STATE_COMPLETED
        assert get_artifact_text(done.artifacts[0]) == "1:orders.cancel"
        # 完了したタスクへの再送は SDK が断る（状態を壊さない）
        with pytest.raises(A2AError):
            await _send(client, "もう一度", task_id=first.id, context_id=first.context_id)


async def test_task_of_other_workspace_is_not_found(engine: AsyncEngine, tmp_path: Path) -> None:
    handlers: dict[SkillId, SkillHandler] = {"spec_review": _echo}
    async with _app(engine, tmp_path, handlers=handlers, team="e2e-a2a-ws-a") as (_, a):
        task = await _send(a, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
        assert (await a.get_task(GetTaskRequest(id=task.id))).id == task.id
    async with _app(engine, tmp_path, handlers=handlers, team="e2e-a2a-ws-b") as (_, b):
        with pytest.raises(TaskNotFoundError):
            await b.get_task(GetTaskRequest(id=task.id))


async def test_audit_is_persisted_even_when_handler_raises(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    async def boom(call: SkillCall) -> None:
        call.audit.record_denied(ToolCall("repo_write", {}), "not_permitted", True)
        raise RuntimeError("x")

    async with _app(engine, tmp_path, handlers={"spec_review": boom}) as (_, client):
        task = await _send(client, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert await _audit_actions(engine, task.id) == ["a2a.tool"]


async def test_unknown_skill_fails_with_explanation(engine: AsyncEngine, tmp_path: Path) -> None:
    async with _app(engine, tmp_path, handlers={}) as (_, client):
        task = await _send(client, "スキルなしの本文")
        assert task.status.state == TaskState.TASK_STATE_FAILED
        assert "spec_review" in get_message_text(task.status.message)
