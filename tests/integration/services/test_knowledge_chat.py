"""対話による知識の作成のサービス（S73）。実 PostgreSQL と台本の LLM（ネットワークなし）。

C8（保留 → ロックなしの LLM → 確かめて確定、冪等キー、途中の削除）、C7（予約と精算）、
C2（持ち主だけ）、メンバーの削除で会話が消えることを確かめる。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.db.repositories.knowledge_chats import KnowledgeChatRepository
from speclino.domain.knowledge_chat import ChatMessage
from speclino.ports.text_llm import JsonGeneration
from speclino.services import knowledge_chat as kc
from speclino.services import members
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.knowledge_chat_budget import ChatBudget, ChatLimits
from tests.integration.services.conftest import register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]
MODEL = "e2e-chat-svc"
REPLY = json.dumps(
    {"explanation": "本文を足しました", "proposal": {"body": "# 指示\n- テストを書く\n"}},
    ensure_ascii=False,
)


class FakeLlm:
    """台本の TextLlm。`hook` は応答の前に呼ばれる（途中の削除・失敗を作る）。"""

    model = MODEL
    max_output_tokens = 4096

    def __init__(self, response: str = REPLY) -> None:
        self.response = response
        self.truncated = False
        self.prompts: list[str] = []
        self.hook: Callable[[], Awaitable[None]] | None = None

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        if self.hook is not None:
            await self.hook()
        return JsonGeneration(self.response, 1000, 100, self.truncated)


@dataclass
class Ctx:
    engine: AsyncEngine
    deps: kc.ChatDeps
    llm: FakeLlm
    slug: str
    other: str


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine) -> AsyncIterator[Ctx]:
    slug = f"e2e-kc-{uuid.uuid4().hex[:8]}"
    other = f"e2e-kco-{uuid.uuid4().hex[:8]}"
    await register_repo(
        engine, f"e2e/{slug}", {"owner1": "owner", "alice": "member", "bob": "member"}, slug
    )
    await register_repo(engine, f"e2e/{other}", {"alice": "member"}, other)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    guard = CostGuard(
        InMemoryLedger([]),
        Decimal("1000"),
        per_run_limit_usd=Decimal("0"),
        pricing={MODEL: (Decimal("1"), Decimal("1"))},
    )
    llm = FakeLlm()
    deps = kc.ChatDeps(factory, llm, ChatBudget(factory, guard, ChatLimits()), guard)
    try:
        yield Ctx(engine, deps, llm, slug, other)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sql_text("DELETE FROM cost_ledger WHERE model = :m"), {"m": MODEL})


async def _create(ctx: Ctx, owner: str = "alice", kind: str = "instructions") -> str:
    async with ctx.deps.session_factory() as s, s.begin():
        chat = await kc.create_chat(s, workspace=ctx.slug, owner=owner, kind=kind)  # type: ignore[arg-type]
    return chat.id


async def _send(ctx: Ctx, chat_id: str, key: str = "key-0001", owner: str = "alice") -> Any:
    return await kc.post_message(
        ctx.deps,
        workspace=ctx.slug,
        owner=owner,
        chat_id=chat_id,
        text="指示書を作りたい",
        idempotency_key=key,
        editor_text="# 指示\n",
    )


async def test_message_is_answered_and_saved_with_proposal(ctx: Ctx) -> None:
    cid = await _create(ctx)
    turn = await _send(ctx, cid)
    assert turn.reply is not None and turn.reply.text == "本文を足しました"
    proposal = turn.reply.proposal
    assert proposal is not None and proposal.valid
    assert proposal.raw_proposal == {"body": "# 指示\n- テストを書く\n"}
    assert proposal.base_hash.startswith("sha256:")
    assert [m.status for m in turn.chat.messages] == ["done", "done"]
    assert turn.chat.title == "指示書を作りたい"
    async with ctx.deps.session_factory() as s:
        saved = await kc.get_chat(s, workspace=ctx.slug, owner="alice", chat_id=cid)
        used = await CostLedgerRepository(s).total_for_day(ctx.deps.today(), uid="alice")
    assert saved.messages == turn.chat.messages
    # 予約は実費に精算されている（1000 + 100 トークン × 1 USD / 1M）
    assert used == Decimal("0.0011")


async def test_same_idempotency_key_does_not_call_llm(ctx: Ctx) -> None:
    cid = await _create(ctx)
    first = await _send(ctx, cid)
    again = await _send(ctx, cid)
    assert len(ctx.llm.prompts) == 1
    assert again.replayed and again.reply == first.reply
    assert len(again.chat.messages) == 2


async def test_pending_key_is_rejected_while_processing(ctx: Ctx) -> None:
    cid = await _create(ctx)
    release = asyncio.Event()
    started = asyncio.Event()

    async def wait() -> None:
        started.set()
        await release.wait()

    ctx.llm.hook = wait
    task = asyncio.create_task(_send(ctx, cid))
    await started.wait()
    with pytest.raises(kc.ChatError) as e:
        await _send(ctx, cid)
    assert e.value.code == "message_pending"
    release.set()
    await task


async def test_chat_deleted_during_llm_discards_reply(ctx: Ctx) -> None:
    cid = await _create(ctx)

    async def delete() -> None:
        async with ctx.deps.session_factory() as s, s.begin():
            await kc.delete_chat(s, workspace=ctx.slug, owner="alice", chat_id=cid)

    ctx.llm.hook = delete
    with pytest.raises(kc.ChatError) as e:
        await _send(ctx, cid)
    assert e.value.code == "chat_not_found"
    async with ctx.engine.connect() as conn:
        n = (
            await conn.execute(
                sql_text("SELECT count(*) FROM knowledge_chats WHERE id = :id"), {"id": cid}
            )
        ).scalar_one()
    assert n == 0


async def test_llm_failure_settles_and_marks_failed(ctx: Ctx) -> None:
    cid = await _create(ctx)

    async def boom() -> None:
        raise RuntimeError("provider said: 秘密のプロンプト")

    ctx.llm.hook = boom
    with pytest.raises(kc.ChatError) as e:
        await _send(ctx, cid)
    assert e.value.code == "chat_failed" and e.value.__cause__ is None
    async with ctx.deps.session_factory() as s:
        chat = await kc.get_chat(s, workspace=ctx.slug, owner="alice", chat_id=cid)
        used = await CostLedgerRepository(s).total_for_day(ctx.deps.today(), uid="alice")
    assert [m.status for m in chat.messages] == ["failed"]
    assert used == Decimal("0")  # 予約は丸ごと戻る
    # 同じキーは前の結果（失敗）を返し、LLM を呼ばない
    ctx.llm.hook = None
    replay = await _send(ctx, cid)
    assert replay.replayed and replay.reply is None and len(ctx.llm.prompts) == 1


async def test_broken_reply_is_a_failure(ctx: Ctx) -> None:
    cid = await _create(ctx)
    ctx.llm.response = "not json"
    with pytest.raises(kc.ChatError) as e:
        await _send(ctx, cid)
    assert e.value.code == "chat_failed"


async def test_message_limit_marks_failed(ctx: Ctx) -> None:
    deps = kc.ChatDeps(
        ctx.deps.session_factory,
        ctx.llm,
        ChatBudget(ctx.deps.session_factory, ctx.deps.cost_guard, ChatLimits(1)),
        ctx.deps.cost_guard,
    )
    cid = await _create(ctx)
    await kc.post_message(
        deps, workspace=ctx.slug, owner="alice", chat_id=cid, text="a", idempotency_key="key-a001"
    )
    with pytest.raises(kc.ChatError) as e:
        await kc.post_message(
            deps,
            workspace=ctx.slug,
            owner="alice",
            chat_id=cid,
            text="b",
            idempotency_key="key-b001",
        )
    assert e.value.code == "message_limit"
    assert len(ctx.llm.prompts) == 1


async def test_input_too_large(ctx: Ctx) -> None:
    cid = await _create(ctx)
    with pytest.raises(kc.ChatError) as e:
        await kc.post_message(
            ctx.deps,
            workspace=ctx.slug,
            owner="alice",
            chat_id=cid,
            text="x",
            idempotency_key="key-big1",
            editor_text="a" * (64 * 1024 + 1),
        )
    assert e.value.code == "input_too_large"
    # 必須部分が 64KB を超える（本文 64KB ＋規則）: 発言は failed になり、LLM は呼ばない
    with pytest.raises(kc.ChatError) as e:
        await kc.post_message(
            ctx.deps,
            workspace=ctx.slug,
            owner="alice",
            chat_id=cid,
            text="x",
            idempotency_key="key-big2",
            editor_text="a" * (64 * 1024 - 10),
        )
    assert e.value.code == "input_too_large" and ctx.llm.prompts == []


async def test_others_cannot_use_my_chat(ctx: Ctx) -> None:
    cid = await _create(ctx)
    for owner in ("bob", "owner1"):
        with pytest.raises(kc.ChatError) as e:
            await _send(ctx, cid, owner=owner)
        assert e.value.code == "chat_not_found"
    with pytest.raises(kc.ChatError):
        await kc.post_message(
            ctx.deps,
            workspace=ctx.other,
            owner="alice",
            chat_id=cid,
            text="x",
            idempotency_key="key-oth1",
        )


async def test_prompt_has_only_own_workspace_names_and_own_conversation(ctx: Ctx) -> None:
    async with ctx.deps.session_factory() as s, s.begin():
        await s.execute(
            sql_text(
                "INSERT INTO artifacts (id, workspace, kind, name) VALUES "
                "(gen_random_uuid(), :a, 'skill', 'mine-skill'), "
                "(gen_random_uuid(), :b, 'skill', 'other-skill')"
            ),
            {"a": ctx.slug, "b": ctx.other},
        )
    bob = await _create(ctx, owner="bob")
    await kc.post_message(
        ctx.deps,
        workspace=ctx.slug,
        owner="bob",
        chat_id=bob,
        text="ボブの秘密の相談",
        idempotency_key="key-bob1",
    )
    cid = await _create(ctx)
    await _send(ctx, cid)
    prompt = ctx.llm.prompts[-1]
    assert "skill:mine-skill" in prompt and "other-skill" not in prompt
    assert "ボブの秘密の相談" not in prompt


async def test_binding_is_checked(ctx: Ctx) -> None:
    async with ctx.deps.session_factory() as s, s.begin():
        rows = await s.execute(
            sql_text(
                "INSERT INTO artifacts (id, workspace, kind, name) VALUES "
                "(gen_random_uuid(), :a, 'spec', 's1'), "
                "(gen_random_uuid(), :b, 'spec', 's2') RETURNING id, workspace"
            ),
            {"a": ctx.slug, "b": ctx.other},
        )
        ids = {ws: str(i) for i, ws in rows.all()}
    cid = await _create(ctx, kind="spec")
    async with ctx.deps.session_factory() as s, s.begin():
        for bad in (ids[ctx.other], "not-a-uuid"):
            with pytest.raises(kc.ChatError) as e:
                await kc.patch_chat(
                    s,
                    workspace=ctx.slug,
                    owner="alice",
                    chat_id=cid,
                    expected_version=1,
                    artifact_id=bad,
                )
            assert e.value.code == "invalid_binding"
        bound = await kc.patch_chat(
            s,
            workspace=ctx.slug,
            owner="alice",
            chat_id=cid,
            expected_version=1,
            artifact_id=ids[ctx.slug],
        )
        assert bound.artifact_id == ids[ctx.slug] and bound.version == 2
        with pytest.raises(kc.ChatError) as e:
            await kc.patch_chat(
                s, workspace=ctx.slug, owner="alice", chat_id=cid, expected_version=1, title="x"
            )
        assert e.value.code == "version_conflict"
    policy = await _create(ctx, kind="policy")
    async with ctx.deps.session_factory() as s, s.begin():
        with pytest.raises(kc.ChatError) as e:
            await kc.patch_chat(
                s,
                workspace=ctx.slug,
                owner="alice",
                chat_id=policy,
                expected_version=1,
                artifact_id=ids[ctx.slug],
            )
        assert e.value.code == "invalid_binding"


async def test_summary_is_not_saved(ctx: Ctx) -> None:
    cid = await _create(ctx)
    with pytest.raises(kc.ChatError) as e:
        await kc.summarize(ctx.deps, workspace=ctx.slug, owner="alice", chat_id=cid)
    assert e.value.code == "nothing_to_summarize"
    await _send(ctx, cid)
    ctx.llm.response = json.dumps(
        {"what": "指示書を作った", "why": "テストを書かせるため", "dropped": "なし"},
        ensure_ascii=False,
    )
    draft = await kc.summarize(ctx.deps, workspace=ctx.slug, owner="alice", chat_id=cid)
    assert draft.note.startswith("何を: 指示書を作った") and len(draft.note) <= 500
    async with ctx.deps.session_factory() as s:
        chat = await kc.get_chat(s, workspace=ctx.slug, owner="alice", chat_id=cid)
    assert len(chat.messages) == 2 and chat.version == 3


async def test_audit_has_no_bodies(ctx: Ctx) -> None:
    cid = await _create(ctx)
    await _send(ctx, cid)
    async with ctx.deps.session_factory() as s, s.begin():
        await kc.delete_chat(s, workspace=ctx.slug, owner="alice", chat_id=cid)
    async with ctx.engine.connect() as conn:
        rows = (
            await conn.execute(
                sql_text(
                    "SELECT action, target, decision, detail::text FROM audit_events "
                    "WHERE target LIKE :t ORDER BY ts"
                ),
                {"t": f"{ctx.slug}/chat/%"},
            )
        ).all()
    assert [r[0] for r in rows] == [
        "knowledge.chat.create",
        "knowledge.chat.message",
        "knowledge.chat.delete",
    ]
    for _, _, _, detail in rows:
        assert set(json.loads(detail)) <= {"chat_id", "message_count", "error"}
        assert "指示" not in detail and "テスト" not in detail


async def test_member_removal_deletes_their_chats(ctx: Ctx) -> None:
    await _create(ctx, owner="bob")
    await _create(ctx, owner="alice")
    async with ctx.deps.session_factory() as s, s.begin():
        await members.remove_member(s, slug=ctx.slug, actor_uid="owner1", target_uid="bob")
    async with ctx.deps.session_factory() as s:
        assert await kc.list_chats(s, workspace=ctx.slug, owner="bob") == []
        assert len(await kc.list_chats(s, workspace=ctx.slug, owner="alice")) == 1
    async with ctx.engine.connect() as conn:
        detail = (
            await conn.execute(
                sql_text(
                    "SELECT detail::text FROM audit_events "
                    "WHERE action = 'knowledge.chat.purge' AND target = :t"
                ),
                {"t": f"{ctx.slug}/chat/*"},
            )
        ).scalar_one()
    assert json.loads(detail) == {"chat_id": None, "message_count": 1}


# ------------------------------------------------------------------ fix round 1
async def test_reply_cut_at_max_tokens_is_saved_without_proposal(ctx: Ctx) -> None:
    """出力の上限で切れた返答は、提案を省いて説明と知らせだけで確定する。"""
    cid = await _create(ctx)
    ctx.llm.response = '{"explanation": "見出しを足します", "proposal": {"body": "# 長い'
    ctx.llm.truncated = True
    turn = await _send(ctx, cid)
    assert turn.reply is not None and turn.reply.proposal is None
    assert turn.reply.text == f"見出しを足します\n\n{kc.TRUNCATED_NOTICE}"
    assert [m.status for m in turn.chat.messages] == ["done", "done"]


async def test_cut_json_without_flag_is_also_truncation(ctx: Ctx) -> None:
    cid = await _create(ctx)
    ctx.llm.response = '{"explanation": "途中で'
    turn = await _send(ctx, cid)
    assert turn.reply is not None and turn.reply.text == kc.TRUNCATED_NOTICE


async def test_reservation_uses_16k_output_tokens(ctx: Ctx) -> None:
    seen: list[Decimal] = []
    budget = ctx.deps.budget
    real = budget.reserve

    async def spy(*args: Any, **kw: Any) -> Any:
        seen.append(args[2])
        return await real(*args, **kw)

    budget.reserve = spy  # type: ignore[method-assign]
    cid = await _create(ctx)
    await _send(ctx, cid)
    prompt_bytes = len(ctx.llm.prompts[0].encode())
    assert kc.CHAT_OUTPUT_TOKENS == 16384
    assert seen == [Decimal(prompt_bytes + 16384) / Decimal(1_000_000)]


async def test_timeout_keeps_the_worst_case_reservation(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kc, "LLM_TIMEOUT_SECONDS", 0.05)

    async def slow() -> None:
        await asyncio.sleep(1)

    ctx.llm.hook = slow
    cid = await _create(ctx)
    with pytest.raises(kc.ChatError) as e:
        await _send(ctx, cid)
    assert e.value.code == "chat_failed"
    async with ctx.deps.session_factory() as s:
        used = await CostLedgerRepository(s).total_for_day(ctx.deps.today(), uid="alice")
    assert used > Decimal("0.016")  # 出力 16,384 トークン分の最悪の額が残る（過少に数えない）


async def test_stale_pending_is_failed_before_processing(ctx: Ctx) -> None:
    cid = await _create(ctx)
    old = datetime.now(UTC) - kc.PENDING_STALE_AFTER - timedelta(seconds=1)
    stuck = ChatMessage(
        id="stuck",
        role="user",
        text="止まった発言",
        idempotency_key="key-stuck",
        status="pending",
        created_at=old,
    )
    async with ctx.deps.session_factory() as s, s.begin():
        await KnowledgeChatRepository(s).save_messages(ctx.slug, "alice", uuid.UUID(cid), [stuck])
    replay = await _send(ctx, cid, key="key-stuck")
    assert replay.replayed and replay.reply is None and ctx.llm.prompts == []
    assert [m.status for m in replay.chat.messages] == ["failed"]


async def test_message_too_long_is_its_own_code(ctx: Ctx) -> None:
    cid = await _create(ctx)
    with pytest.raises(kc.ChatError) as e:
        await kc.post_message(
            ctx.deps,
            workspace=ctx.slug,
            owner="alice",
            chat_id=cid,
            text="あ" * 2731,  # 8,193 バイト
            idempotency_key="key-long",
        )
    assert e.value.code == "message_too_long"
