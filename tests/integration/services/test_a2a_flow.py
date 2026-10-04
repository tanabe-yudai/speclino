"""A2A（S59）: Agent Card の公開、トークン認証、入力待ちの往復、DB のタスク、監査の保存。

前半はスキルの中身を偽の処理（`_echo`）に差し替えて入口の骨組みを確かめる。後半は本物の
`build_handlers`（LLM だけ台本）で 3 スキルを公式 SDK のクライアントから通す。"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.helpers import get_artifact_text, get_data_parts, get_message_text, new_text_part
from a2a.types import (
    Artifact,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import A2AError, InvalidParamsError, TaskNotFoundError
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.a2a_server.card import build_agent_card
from speclino.a2a_server.executor import SkillCall, SkillHandler, SkillsUnavailable
from speclino.a2a_server.handlers import build_deps, build_handlers, handlers_for
from speclino.adapters.llm.scripted import ScriptedJsonLlm, ScriptedLlm, text
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.agent.gateway import ToolCall
from speclino.api.app import create_app
from speclino.api.deps import AppState, _SessionLedger
from speclino.domain.a2a import SkillId
from speclino.services.cost_guard import CostGuard
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.services.run_view import run_view
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo, repo_id_of
from tests.integration.services.test_mcp_flow import _issue
from tests.integration.services.test_review_run_service import git, make_demo_repo
from tests.unit.agent.consultant.test_spec_review import AMBIGUOUS, CLEAR, REVISION, SPEC_MD

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
    handlers: (
        dict[SkillId, SkillHandler] | Callable[[AppState], dict[SkillId, SkillHandler] | None]
    ),
    team: str = A2A_TEAM,
    host: LocalCodeHost | None = None,
    cost_guard: CostGuard | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, Client]]:
    """ワークスペースとトークンを作り、A2A を有効にしたアプリとクライアントを返す。

    handlers は dict か、state を受けて dict を返す関数（本物の build_handlers を使うとき）。
    関数が None を返せば上書きせず、executor が state から本物を組み立てる。
    """
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO teams (team, approvers) VALUES (:t, '[]') ON CONFLICT DO NOTHING"
            ),
            {"t": team},
        )
    token = await _issue(engine, team)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = host or LocalCodeHost(repos={})
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", code_host_kind="local", a2a_enabled=True)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(
        factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False), cost_guard=cost_guard
    )
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service)
    state.a2a_handlers = handlers(state) if callable(handlers) else handlers
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


@pytest.mark.parametrize("field", ["context_id", "task_id"])
async def test_overlong_ids_are_rejected_as_invalid_params(
    engine: AsyncEngine, tmp_path: Path, field: str
) -> None:
    """列（36 文字）より長い ID は DB に届く前に A2A の InvalidParams で断る（500 にしない）。"""
    long_id = "x" * 37
    async with _app(engine, tmp_path, handlers={"spec_review": _echo}) as (http, client):
        with pytest.raises(InvalidParamsError):
            await _send(client, "---\nskill: spec_review\nrepo: o/r\n---\n本文", **{field: long_id})
        # 素の JSON-RPC でも HTTP 500 ではなく、JSON-RPC の invalid params（-32602）が返る
        raw = await http.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "messageId": uuid.uuid4().hex,
                        "parts": [{"text": "本文"}],
                        "contextId" if field == "context_id" else "taskId": long_id,
                    }
                },
            },
        )
        assert raw.status_code != 500, raw.text
        assert raw.json()["error"]["code"] == -32602, raw.text
        # 長い ID のタスクは「無い」（参照も DB の失敗にしない）
        with pytest.raises(TaskNotFoundError):
            await client.get_task(GetTaskRequest(id=long_id))


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


async def test_cancel_or_send_to_task_of_other_workspace_is_not_found(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """B のトークンで A のタスクに送っても取り消しても「無い」。A のタスクは変わらない。"""
    handlers: dict[SkillId, SkillHandler] = {"spec_review": _echo}
    async with _app(engine, tmp_path, handlers=handlers, team="e2e-a2a-ws-a") as (_, a):
        task = await _send(a, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    async with _app(engine, tmp_path, handlers=handlers, team="e2e-a2a-ws-b") as (_, b):
        with pytest.raises(TaskNotFoundError):
            await _send(b, "orders.cancel", task_id=task.id, context_id=task.context_id)
        with pytest.raises(TaskNotFoundError):
            await b.cancel_task(CancelTaskRequest(id=task.id))
    async with _app(engine, tmp_path, handlers=handlers, team="e2e-a2a-ws-a") as (_, a):
        after = await a.get_task(GetTaskRequest(id=task.id))
        assert after.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert len(after.history) == len(task.history)
        assert not after.artifacts


async def test_skills_fail_closed_without_cost_guard(engine: AsyncEngine, tmp_path: Path) -> None:
    """費用ガードの無い構成では組み立てず、タスクは理由つきで失敗する（計量なしで動かさない）。"""

    def no_override(state: AppState) -> dict[SkillId, SkillHandler] | None:
        with pytest.raises(SkillsUnavailable, match="費用ガード"):
            build_handlers(state, spec_llm=ScriptedJsonLlm("{}"), agent_model="scripted")
        return None  # executor に本物の組み立てをさせる

    async with _app(engine, tmp_path, handlers=no_override) as (_, client):
        task = await _send(client, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert "費用ガード" in get_message_text(task.status.message)


async def test_handler_build_error_fails_task_with_reason(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """組み立てが想定外の例外で落ちても、タスクは理由つきの failed になる（SDK に投げない）。"""

    def no_override(state: AppState) -> None:
        return None

    async with _app(engine, tmp_path, handlers=no_override) as (_, client):
        with patch("speclino.a2a_server.mount.build_handlers", side_effect=RuntimeError("context")):
            task = await _send(client, "---\nskill: spec_review\nrepo: o/r\n---\n本文")
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert get_message_text(task.status.message) == "処理に失敗しました: RuntimeError"


SPEC_MD_PATH = "specs/orders-cancel.md"


def _make_repo_with_spec_lines(root: Path) -> tuple[str, int]:
    """デモのリポジトリの head に、R-CANCEL-04 の仕様の位置（パスと行）を足したコミットを積む。

    共有の examples/orders の Spec IR には位置が無い。足すと Rule のハッシュ（承認の対象）が
    変わり、他の試験と評価が依存する値が動くので、このテストの一時リポジトリだけで足す。
    返すのは足したコミットと、見出しの行番号。
    """
    _, head, _ = make_demo_repo(root)
    git(root, "checkout", "-q", head)
    md_lines = (root / SPEC_MD_PATH).read_text(encoding="utf-8").splitlines()
    line = next(i for i, s in enumerate(md_lines, 1) if s.strip() == "### R-CANCEL-04")
    ir_path = root / "specs" / "ir" / "cancel_order.spec-ir.json"
    data = json.loads(ir_path.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        if rule["id"] == "R-CANCEL-04":
            for ref in rule["source_refs"]:
                ref.update(path=SPEC_MD_PATH, line_start=line)
    ir_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    git(root, "commit", "-qam", "head: spec refs with lines")
    return git(root, "rev-parse", "HEAD"), line


def _artifact(task: Task, name: str) -> Artifact:
    found = [a for a in task.artifacts if a.name == name]
    assert found, [a.name for a in task.artifacts]
    return found[0]


def _artifact_data(task: Task, name: str) -> Any:
    [data] = get_data_parts(_artifact(task, name).parts)
    return data


async def test_three_skills_end_to_end_with_real_handlers(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """本物の build_handlers（LLM だけ台本）で 3 スキルを公式 SDK のクライアントから通す。"""
    team = f"e2e-a2a-{uuid.uuid4().hex[:8]}"
    repo = f"{team}/orders"
    await register_repo(engine, repo, {"dev": "member"}, team=team)
    head, spec_line = _make_repo_with_spec_lines(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    ScriptedLlm.reset()
    answer = "R-CANCEL-04 は管理者以外で在庫の復元が呼ばれないため違反です（反例を参照）。"

    def real(state: AppState) -> dict[SkillId, SkillHandler]:
        deps = build_deps(
            state,
            spec_llm=ScriptedJsonLlm([AMBIGUOUS, CLEAR, REVISION]),
            agent_model=ScriptedLlm(steps=[[text(answer)]]),
        )
        deps.poll_seconds = 0.01
        deps.wait_seconds = 10.0
        return handlers_for(deps)

    guard = CostGuard(_SessionLedger(async_sessionmaker(engine)), Decimal("1000"))
    async with _app(engine, tmp_path, handlers=real, team=team, host=host, cost_guard=guard) as (
        http,
        client,
    ):
        # 1. spec_review: 曖昧な点を聞き返し、答えると候補を返す
        asked = await _send(client, f"---\nskill: spec_review\nrepo: {repo}\n---\n{SPEC_MD}")
        assert asked.status.state == TaskState.TASK_STATE_INPUT_REQUIRED, get_message_text(
            asked.status.message
        )
        assert "すぐに" in get_message_text(asked.status.message)
        reviewed = await _send(client, "1. 5 秒以内", task_id=asked.id, context_id=asked.context_id)
        assert reviewed.status.state == TaskState.TASK_STATE_COMPLETED, get_message_text(
            reviewed.status.message
        )
        candidate = _artifact_data(reviewed, "spec-ir-candidate.json")
        assert [r["id"] for r in candidate["rules"]] == ["R-CANCEL-04"]

        # 2. requirement_consult: head の検査を起動して待ち、検査器の結果をそのまま返す
        consulted = await _send(
            client,
            f"---\nskill: requirement_consult\nrepo: {repo}\nrequirement_id: R-CANCEL-04\n"
            f"commit: {head}\n---\nなぜ違反になるのか",
        )
        assert consulted.status.state == TaskState.TASK_STATE_COMPLETED, get_message_text(
            consulted.status.message
        )
        answer_md = get_artifact_text(_artifact(consulted, "answer.md"))
        assert answer_md.startswith(answer)
        assert "verification.json" in answer_md and "PR で人が" in answer_md  # 免責を本体に持つ
        verification = _artifact_data(consulted, "verification.json")
        assert verification["exec_state"] in ("completed", "awaiting_answer"), verification
        violated = [f for f in verification["findings"] if f["verdict"] == "VIOLATED"]
        assert [f["requirement_id"] for f in violated] == ["R-CANCEL-04"]
        finding = violated[0]
        assert finding["id"] and finding["spec_refs"] and finding["counterexample"], finding
        spec_ref = finding["spec_refs"][0]
        assert spec_ref["requirement_id"] == "R-CANCEL-04"
        # 根拠の仕様の位置（ファイルと行）まで返す
        assert (spec_ref["path"], spec_ref["line_start"]) == (SPEC_MD_PATH, spec_line), spec_ref
        run_id = verification["run_id"]
        # 切り詰めず、DB の Finding（payload 全体）と同じものを返している
        stored = await run_view(async_sessionmaker(engine), uuid.UUID(run_id), team, full=True)
        assert stored is not None
        assert [f for f in stored["findings"] if f["id"] == finding["id"]] == [finding]

        # 3. run_clarification: この検査に要確認は無い
        assert not [f for f in verification["findings"] if f["verdict"] == "REVIEW"]
        clarify = f"---\nskill: run_clarification\nrun_id: {run_id}\n---\n"
        none_left = await _send(client, clarify)
        assert none_left.status.state == TaskState.TASK_STATE_COMPLETED
        assert "ありません" in get_message_text(none_left.status.message)
        # 要確認の聞き取りと補足の記録も本物の経路で通すため、検査器の結果の 1 件を DB で
        # REVIEW に書き換える（デモの検査は要確認を出さない。テストのための細工）
        async with engine.begin() as conn:
            await conn.execute(
                sql_text(
                    "UPDATE findings SET verdict = 'REVIEW', "
                    "payload = jsonb_set(payload, '{verdict}', '\"REVIEW\"') "
                    "WHERE run_id = :r AND requirement_id = 'R-CANCEL-01'"
                ),
                {"r": run_id},
            )
        asked_review = await _send(client, clarify)
        assert asked_review.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert "R-CANCEL-01" in get_message_text(asked_review.status.message)
        clarified = await _send(
            client,
            "所有者だけが取り消せる意図",
            task_id=asked_review.id,
            context_id=asked_review.context_id,
        )
        assert clarified.status.state == TaskState.TASK_STATE_COMPLETED, get_message_text(
            clarified.status.message
        )
        assert _artifact_data(clarified, "supplements.json")[0]["requirement_id"] == "R-CANCEL-01"
        # 補足は web の検査の詳細（Task 9 の API）でワークスペースのメンバーに見える
        url = f"/api/review-runs/{run_id}/supplements"
        shown = await http.get(url, headers={"X-Dev-User": "dev"})
        assert shown.status_code == 200, shown.text
        [supplement] = shown.json()["supplements"]
        assert (supplement["requirement_id"], supplement["content"]) == (
            "R-CANCEL-01",
            "所有者だけが取り消せる意図",
        )
        outsider = await http.get(url, headers={"X-Dev-User": f"outsider-{uuid.uuid4().hex}"})
        assert outsider.status_code == 404
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sql_text(
                    "SELECT requirement_id, content, a2a_task_id FROM agent_supplements "
                    "WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("R-CANCEL-01", "所有者だけが取り消せる意図", clarified.id)]

    # 4. 4 つのタスクのどれにも門番の監査（a2a.tool）が残る
    for task in (reviewed, consulted, none_left, clarified):
        assert "a2a.tool" in await _audit_actions(engine, task.id), task.id
