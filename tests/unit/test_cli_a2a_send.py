"""`speclino a2a-send`（S59）: トークンは環境変数だけ、入力待ちの往復を表示する。

サーバは DB なしの最小の A2A アプリ（本物のスキルの代わりに `_Echo`）を `httpx.ASGITransport`
で直接つなぐ。"""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from fastapi import FastAPI, Request, Response
from typer.testing import CliRunner

from speclino import cli
from speclino.a2a_server.card import A2A_PATH, build_agent_card
from speclino.agent.consultant.request import parse_skill_request

# ダミーの値は実行時に作る（固定の文字列は秘密の検出に掛かる）
TOKEN = "slmcp_" + secrets.token_urlsafe(16)
runner = CliRunner()


class _Echo(AgentExecutor):
    """1 回目はスキルの指定を読んで質問し、2 回目は答えを成果物にして完了する。"""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        assert message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        if context.current_task is None:
            req = parse_skill_request(message)
            await updater.requires_input(
                updater.new_agent_message([new_text_part(f"{req.skill}/{req.repo}: behavior は？")])
            )
            return
        await updater.add_artifact(
            [new_text_part(f"答え={context.get_user_input()}")], name="result"
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _server() -> httpx.ASGITransport:
    card = build_agent_card("http://test")
    app = FastAPI()

    @app.middleware("http")
    async def _auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            request.url.path == A2A_PATH
            and request.headers.get("authorization") != f"Bearer {TOKEN}"
        ):
            return Response(status_code=401)
        return await call_next(request)

    handler = DefaultRequestHandler(_Echo(), InMemoryTaskStore(), card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, A2A_PATH),
    )
    return httpx.ASGITransport(app=app)


@pytest.fixture(autouse=True)
def _fake_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI の送り先を、テストごとに 1 つのアプリ（タスクの保存は共有）に差し替える。"""
    transport = _server()
    real = cli._client
    monkeypatch.setattr(cli, "_client", lambda url, token, _t=None: real(url, token, transport))


def test_missing_token_exits_2_and_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECLINO_TOKEN", raising=False)
    result = runner.invoke(cli.app, ["a2a-send", "--url", "http://test", "--skill", "spec_review"])
    assert result.exit_code == 2
    assert "SPECLINO_TOKEN" in result.output


def test_round_trip_prints_question_ids_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECLINO_TOKEN", TOKEN)
    first = runner.invoke(
        cli.app,
        ["a2a-send", "--url", "http://test", "--skill", "spec_review", "--repo", "o/r", "本文"],
    )
    assert first.exit_code == 0, first.output
    assert "INPUT_REQUIRED" in first.output
    assert "spec_review/o/r: behavior は？" in first.output
    assert TOKEN not in first.output
    task_id = re.search(r"task_id: (\S+)", first.output)
    context_id = re.search(r"context_id: (\S+)", first.output)
    assert task_id and context_id

    second = runner.invoke(
        cli.app,
        [
            "a2a-send", "--url", "http://test", "--task-id", task_id.group(1),
            "--context-id", context_id.group(1), "orders.cancel",
        ],
    )  # fmt: skip
    assert second.exit_code == 0, second.output
    assert "COMPLETED" in second.output
    assert "result" in second.output and "答え=orders.cancel" in second.output


def test_front_matter_text_is_sent_as_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SPECLINO_TOKEN", TOKEN)
    md = tmp_path / "msg.md"
    md.write_text(
        "---\nskill: requirement_consult\nrepo: o/r\nrequirement_id: R-1\n---\n質問", "utf-8"
    )
    result = runner.invoke(cli.app, ["a2a-send", "--url", "http://test", "--file", str(md)])
    assert result.exit_code == 0, result.output
    assert "requirement_consult/o/r" in result.output


def test_wrong_token_fails_with_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECLINO_TOKEN", "slmcp_wrong")
    result = runner.invoke(
        cli.app, ["a2a-send", "--url", "http://test", "--skill", "spec_review", "x"]
    )
    assert result.exit_code != 0
    assert "slmcp_wrong" not in result.output


def test_unreadable_file_exits_2_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SPECLINO_TOKEN", TOKEN)
    missing = tmp_path / "missing.md"
    result = runner.invoke(cli.app, ["a2a-send", "--url", "http://test", "--file", str(missing)])
    assert result.exit_code == 2
    assert "missing.md" in result.output
    assert not isinstance(result.exception, OSError)


def test_client_waits_long_enough_for_slow_skills() -> None:
    assert cli._client("http://test", TOKEN).timeout.read == 360.0
