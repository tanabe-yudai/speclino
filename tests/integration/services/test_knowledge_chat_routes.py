"""`/api/workspaces/{slug}/knowledge/chats`（S73）の統合試験。実 PostgreSQL と台本の LLM。

C2: 会話は持ち主だけ（同じワークスペースの owner でも 404）。C9: エラーは固定のコードだけで、
入力・LLM の生の応答・プロンプト・事業者の例外の文を、応答にも監査にもログにも出さない。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.api.routes import knowledge_chat as routes
from speclino.ports.text_llm import JsonGeneration, JsonStreamEvent
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

ALICE = {"X-Dev-User": "alice"}
OWNER = {"X-Dev-User": "owner1"}
STRANGER = {"X-Dev-User": "owner9"}
MODEL = "e2e-chat-routes"
SECRET = "TOPSECRET-4242"
REPLY = json.dumps(
    {"explanation": "了解しました", "proposal": {"markdown": "# 仕様\n", "name": "x"}},
    ensure_ascii=False,
)


class FakeLlm:
    model = MODEL
    max_output_tokens = 4096

    def __init__(self) -> None:
        self.response = REPLY
        self.error: Exception | None = None
        self.prompts: list[str] = []
        self.delay = 0.0

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return JsonGeneration(self.response, 100, 10)

    async def stream_json(
        self, prompt: str, schema: dict[str, Any]
    ) -> AsyncIterator[JsonStreamEvent]:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        for i in range(0, len(self.response), 6):
            await asyncio.sleep(self.delay)
            yield self.response[i : i + 6]
        yield JsonGeneration(self.response, 100, 10)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    slug: str
    other: str
    llm: FakeLlm
    engine: AsyncEngine


async def _make(engine: AsyncEngine, tmp_path: Path, limit: str | None) -> AsyncIterator[Ctx]:
    slug = f"e2e-kcr-{uuid.uuid4().hex[:8]}"
    other = f"e2e-kcro-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}", {"owner1": "owner", "alice": "member"}, slug)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    guard = (
        None
        if limit is None
        else CostGuard(
            InMemoryLedger([]),
            Decimal(limit),
            per_run_limit_usd=Decimal("0"),
            pricing={MODEL: (Decimal("1"), Decimal("1"))},
        )
    )
    service = ReviewRunService(
        factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False), cost_guard=guard
    )
    llm = FakeLlm()
    state = AppState(settings, engine, factory, host, queue, blobs, service, knowledge_chat_llm=llm)
    app = create_app(settings, state)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
        ):
            yield Ctx(c, slug, other, llm, engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sql_text("DELETE FROM cost_ledger WHERE model = :m"), {"m": MODEL})


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    async for c in _make(engine, tmp_path, "1000000"):
        yield c


def _url(slug: str, rest: str = "") -> str:
    return f"/api/workspaces/{slug}/knowledge/chats{rest}"


async def _create(ctx: Ctx, kind: str = "spec", headers: dict[str, str] = ALICE) -> dict[str, Any]:
    res = await ctx.client.post(_url(ctx.slug), json={"kind": kind}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()  # type: ignore[no-any-return]


def _msg(
    text: str = "注文の取消の仕様を書きたい", key: str = "key-0001", **extra: Any
) -> dict[str, Any]:
    return {"text": text, "idempotency_key": key, "editor_text": "# 仕様\n", **extra}


async def test_full_flow(ctx: Ctx) -> None:
    chat = await _create(ctx)
    cid = chat["id"]
    res = await ctx.client.post(_url(ctx.slug, f"/{cid}/messages"), json=_msg(), headers=ALICE)
    assert res.status_code == 200, res.text
    turn = res.json()
    assert turn["reply"]["text"] == "了解しました"
    proposal = turn["reply"]["proposal"]
    assert proposal["raw_proposal"] == {"markdown": "# 仕様\n"}  # 名前は捨てる
    assert proposal["base_hash"].startswith("sha256:")
    assert "diagnostics" in proposal

    listed = (await ctx.client.get(_url(ctx.slug, "?kind=spec"), headers=ALICE)).json()
    assert [c["id"] for c in listed] == [cid] and listed[0]["message_count"] == 2
    assert "messages" not in listed[0]
    got = (await ctx.client.get(_url(ctx.slug, f"/{cid}"), headers=ALICE)).json()
    assert len(got["messages"]) == 2

    ctx.llm.response = json.dumps({"what": "仕様を作った", "why": "取消のため", "dropped": "なし"})
    summary = await ctx.client.post(_url(ctx.slug, f"/{cid}/summary"), headers=ALICE)
    assert summary.status_code == 200 and summary.json()["note"].startswith("何を: 仕様を作った")

    patched = await ctx.client.patch(
        _url(ctx.slug, f"/{cid}"),
        json={"expected_version": got["version"], "title": "取消"},
        headers=ALICE,
    )
    assert patched.status_code == 200 and patched.json()["title"] == "取消"
    stale = await ctx.client.patch(
        _url(ctx.slug, f"/{cid}"), json={"expected_version": 1, "title": "y"}, headers=ALICE
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"

    assert (await ctx.client.delete(_url(ctx.slug, f"/{cid}"), headers=ALICE)).status_code == 204
    assert (await ctx.client.get(_url(ctx.slug, f"/{cid}"), headers=ALICE)).status_code == 404


async def test_bind_new_chat_after_first_save(ctx: Ctx) -> None:
    chat = await _create(ctx, kind="instructions")
    created = await ctx.client.post(
        f"/api/workspaces/{ctx.slug}/artifacts",
        json={
            "kind": "instructions",
            "name": "dev-rules",
            "content": {
                "kind": "instructions",
                "target": "agents_md",
                "body": "x",
                "scope": {"workspace": True},
            },
        },
        headers=ALICE,
    )
    assert created.status_code == 200, created.text
    aid = created.json()["artifact_id"]
    res = await ctx.client.patch(
        _url(ctx.slug, f"/{chat['id']}"),
        json={"expected_version": chat["version"], "artifact_id": aid},
        headers=ALICE,
    )
    assert res.status_code == 200 and res.json()["artifact_id"] == aid
    listed = (await ctx.client.get(_url(ctx.slug, f"?artifact_id={aid}"), headers=ALICE)).json()
    assert [c["id"] for c in listed] == [chat["id"]]


async def test_owner_of_the_workspace_cannot_read_my_chat(ctx: Ctx) -> None:
    chat = await _create(ctx)
    cid = chat["id"]
    for method, rest, body in (
        ("GET", f"/{cid}", None),
        ("DELETE", f"/{cid}", None),
        ("PATCH", f"/{cid}", {"expected_version": 1, "title": "x"}),
        ("POST", f"/{cid}/messages", _msg()),
        ("POST", f"/{cid}/summary", None),
    ):
        res = await ctx.client.request(method, _url(ctx.slug, rest), json=body, headers=OWNER)
        assert res.status_code == 404, (method, rest, res.text)
        assert res.json()["error"]["code"] == "chat_not_found"
    assert (await ctx.client.get(_url(ctx.slug), headers=OWNER)).json() == []
    assert ctx.llm.prompts == []


async def test_other_workspace_is_404(ctx: Ctx) -> None:
    chat = await _create(ctx)
    res = await ctx.client.get(_url(ctx.slug), headers=STRANGER)
    assert res.status_code == 404 and res.json()["error"]["code"] == "workspace_not_found"
    res = await ctx.client.get(_url(ctx.other, f"/{chat['id']}"), headers=STRANGER)
    assert res.status_code == 404 and res.json()["error"]["code"] == "chat_not_found"


async def _audit_text(ctx: Ctx) -> str:
    async with ctx.engine.connect() as conn:
        rows = (
            await conn.execute(
                sql_text("SELECT detail::text, target FROM audit_events WHERE target LIKE :t"),
                {"t": f"{ctx.slug}/%"},
            )
        ).all()
    return "\n".join(f"{d} {t}" for d, t in rows)


@pytest.mark.parametrize(
    "body",
    [
        _msg(text=SECRET, extra_field=SECRET),
        _msg(text=SECRET, key=f"bad key {SECRET}"),
        {"text": 12, "idempotency_key": SECRET},
    ],
)
async def test_validation_errors_do_not_echo_input(
    ctx: Ctx, body: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    chat = await _create(ctx)
    res = await ctx.client.post(_url(ctx.slug, f"/{chat['id']}/messages"), json=body, headers=ALICE)
    assert res.status_code == 422
    assert res.json() == {
        "error": {"code": "invalid_request", "message": "リクエストの形式が正しくありません"}
    }
    assert SECRET not in caplog.text and SECRET not in await _audit_text(ctx)


async def test_broken_json_and_bad_kind_are_fixed_errors(ctx: Ctx) -> None:
    res = await ctx.client.post(
        _url(ctx.slug),
        content=f'{{"kind": "{SECRET}"',
        headers={**ALICE, "content-type": "application/json"},
    )
    assert res.status_code == 422 and SECRET not in res.text
    res = await ctx.client.get(_url(ctx.slug, f"?kind={SECRET}"), headers=ALICE)
    assert res.status_code == 422 and SECRET not in res.text


async def test_llm_failure_does_not_leak(ctx: Ctx, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    chat = await _create(ctx)
    ctx.llm.error = RuntimeError(f"provider error with prompt {SECRET}")
    res = await ctx.client.post(
        _url(ctx.slug, f"/{chat['id']}/messages"), json=_msg(text=f"相談 {SECRET}"), headers=ALICE
    )
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "chat_failed" and SECRET not in res.text
    assert SECRET not in caplog.text
    assert "RuntimeError" in caplog.text  # 型名だけは残る
    assert SECRET not in await _audit_text(ctx)


async def test_broken_llm_output_does_not_leak(ctx: Ctx, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    chat = await _create(ctx)
    ctx.llm.response = f"not json {SECRET}"
    res = await ctx.client.post(
        _url(ctx.slug, f"/{chat['id']}/messages"), json=_msg(), headers=ALICE
    )
    assert res.status_code == 502 and SECRET not in res.text and SECRET not in caplog.text


async def test_budget_refusal_is_429(engine: AsyncEngine, tmp_path: Path) -> None:
    async for c in _make(engine, tmp_path, "0.000001"):
        chat = await _create(c)
        res = await c.client.post(
            _url(c.slug, f"/{chat['id']}/messages"), json=_msg(text=SECRET), headers=ALICE
        )
        assert res.status_code == 429 and res.json()["error"]["code"] == "cost_limit"
        assert SECRET not in res.text and c.llm.prompts == []


async def test_no_cost_guard_is_503(engine: AsyncEngine, tmp_path: Path) -> None:
    async for c in _make(engine, tmp_path, None):
        chat = await _create(c)
        res = await c.client.post(
            _url(c.slug, f"/{chat['id']}/messages"), json=_msg(), headers=ALICE
        )
        assert res.status_code == 503 and res.json()["error"]["code"] == "chat_unavailable"


async def test_retry_with_same_key_returns_previous_result(ctx: Ctx) -> None:
    chat = await _create(ctx)
    url = _url(ctx.slug, f"/{chat['id']}/messages")
    first = await ctx.client.post(url, json=_msg(), headers=ALICE)
    again = await ctx.client.post(url, json=_msg(), headers=ALICE)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["reply"] == first.json()["reply"] and len(ctx.llm.prompts) == 1


async def test_message_too_long_is_413_without_echo(ctx: Ctx) -> None:
    chat = await _create(ctx)
    res = await ctx.client.post(
        _url(ctx.slug, f"/{chat['id']}/messages"), json=_msg(text=SECRET * 1000), headers=ALICE
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "message_too_long" and SECRET not in res.text
    assert ctx.llm.prompts == []


async def test_create_rechecks_membership_inside_the_transaction(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """body を読む間にメンバーから外されたら、会話を作らない。"""
    from speclino.api.routes import knowledge_chat as route

    real = route._read_body

    async def remove_then_read(request: Any, model: Any) -> Any:
        async with ctx.engine.begin() as conn:
            await conn.execute(
                sql_text("DELETE FROM workspace_members WHERE team = :t AND uid = 'alice'"),
                {"t": ctx.slug},
            )
        return await real(request, model)

    monkeypatch.setattr(route, "_read_body", remove_then_read)
    res = await ctx.client.post(_url(ctx.slug), json={"kind": "spec"}, headers=ALICE)
    assert res.status_code == 404
    async with ctx.engine.connect() as conn:
        n = (
            await conn.execute(
                sql_text("SELECT count(*) FROM knowledge_chats WHERE workspace = :t"),
                {"t": ctx.slug},
            )
        ).scalar_one()
    assert n == 0


# ---------------------------------------------------------------- ストリーム（S74）
def _parse_sse(body: str) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    events: list[tuple[str, dict[str, Any]]] = []
    comments: list[str] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        if block.startswith(":"):
            comments.append(block)
            continue
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events, comments


async def test_stream_flow_matches_non_stream_shape(ctx: Ctx) -> None:
    cid = (await _create(ctx))["id"]
    res = await ctx.client.post(
        _url(ctx.slug, f"/{cid}/messages/stream"), json=_msg(), headers=ALICE
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache" and res.headers["x-accel-buffering"] == "no"
    events, _ = _parse_sse(res.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert set(kinds[1:-1]) == {"delta", "draft"}
    assert "".join(d["text"] for k, d in events if k == "delta") == "了解しました"
    drafts = [d for k, d in events if k == "draft"]
    assert drafts and drafts[-1] == {"field": "markdown", "text": "# 仕様\n"}
    done = events[-1][1]
    assert done["reply"]["text"] == "了解しました"
    assert done["reply"]["proposal"]["raw_proposal"] == {"markdown": "# 仕様\n"}

    # 非ストリームの結果と同じ形（同じ発言の再送は前の結果をそのまま返す）
    again = await ctx.client.post(_url(ctx.slug, f"/{cid}/messages"), json=_msg(), headers=ALICE)
    assert again.status_code == 200
    assert set(again.json()) == set(done)
    assert again.json()["reply"] == done["reply"]


async def test_stream_error_event_has_fixed_code_only(ctx: Ctx) -> None:
    cid = (await _create(ctx))["id"]
    ctx.llm.error = RuntimeError(f"provider said: {SECRET}")
    res = await ctx.client.post(
        _url(ctx.slug, f"/{cid}/messages/stream"), json=_msg(), headers=ALICE
    )
    assert res.status_code == 200
    events, _ = _parse_sse(res.text)
    assert events[-1] == (
        "error",
        {
            "code": "chat_failed",
            "message": "AI の応答を得られませんでした。もう一度試してください",
            "status": 502,
        },
    )
    assert SECRET not in res.text


async def test_stream_unknown_chat_and_non_member(ctx: Ctx) -> None:
    res = await ctx.client.post(
        _url(ctx.slug, f"/{uuid.uuid4()}/messages/stream"), json=_msg(), headers=ALICE
    )
    events, _ = _parse_sse(res.text)
    assert [(k, d["code"]) for k, d in events] == [("error", "chat_not_found")]
    stranger = await ctx.client.post(
        _url(ctx.slug, f"/{uuid.uuid4()}/messages/stream"), json=_msg(), headers=STRANGER
    )
    assert stranger.status_code == 404
    bad = await ctx.client.post(
        _url(ctx.slug, f"/{uuid.uuid4()}/messages/stream"), json={"text": ""}, headers=ALICE
    )
    assert bad.status_code == 422


async def test_stream_sends_heartbeat_while_waiting(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "HEARTBEAT_SECONDS", 0.05)
    ctx.llm.delay = 0.1
    cid = (await _create(ctx))["id"]
    res = await ctx.client.post(
        _url(ctx.slug, f"/{cid}/messages/stream"), json=_msg(), headers=ALICE
    )
    events, comments = _parse_sse(res.text)
    assert events[-1][0] == "done" and comments and set(comments) == {": ping"}
