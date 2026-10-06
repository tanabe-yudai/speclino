"""対話による知識の作成（S73、design/2026-10-06-knowledge-chat.md）。

- 会話は持ち主だけが読み書き・削除できる（C2）。問い合わせは `KnowledgeChatRepository` が
  必ず (workspace, owner_uid) で絞る。他人の会話は存在しないのと同じ（`chat_not_found`）
- 発言の処理（C8）: 保留の発言を短いトランザクションで足す → ロックを持たずに LLM を呼ぶ →
  行を固定し直して持ち主と保留の発言が残っていることを確かめてから確定する。会話が途中で
  消えていれば応答を捨て、行を復活させない。同じ冪等キーの再送は LLM を呼ばずに前の結果を返す
- 費用（C7）: 呼ぶ前に最悪の額を予約し（`ChatBudget`）、どの終わり方でも精算する
- 秘密（C9）: 失敗は固定のコード（`ChatError.code`）だけを持ち、入力・プロンプト・LLM の
  生の応答・事業者の例外の文を例外にもログにも載せない（ログは例外の型名だけ）。監査は
  `record_chat_event`（chat_id・件数・成否だけ）
- AI は提案を返すだけで、保存・承認には関与しない（C4・C6）
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.repositories.artifacts import ArtifactRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.knowledge_chats import KnowledgeChatRepository
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import SpecContent
from speclino.domain.knowledge_chat import (
    CHAT_MAX_BYTES,
    CHAT_MAX_MESSAGES,
    CHAT_TITLE_MAX_CHARS,
    EDITOR_TEXT_MAX_BYTES,
    MESSAGE_MAX_BYTES,
    NOTE_MAX_CHARS,
    REPLY_MAX_BYTES,
    ChatAuditAction,
    ChatErrorCode,
    ChatMessage,
    ChatProposal,
    ChatSummaryDraft,
    ChatTurn,
    KnowledgeChat,
    KnowledgeChatKind,
    KnowledgeChatSummary,
)
from speclino.domain.spec_ir import SpecIR
from speclino.ports.text_llm import JsonGeneration, TextLlm
from speclino.services.cost_guard import CostGuard
from speclino.services.knowledge_chat_budget import BudgetRefusal, ChatPurpose, ChatReservation
from speclino.services.knowledge_chat_prompt import (
    SUMMARY_SCHEMA,
    PromptTooLarge,
    build_chat_prompt,
    build_summary_prompt,
)
from speclino.services.knowledge_chat_proposal import build_proposal, response_schema, text_hash

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 120.0
# 会話の 1 回の出力トークンの上限（設計 C7。費用の予約もこの値で見積もる）
CHAT_OUTPUT_TOKENS = 4096
# これより古い保留の発言は、処理が途中で止まったものとして扱う（再送を永久に断らない）
PENDING_STALE_AFTER = timedelta(minutes=10)
_EXPLANATION_MAX_CHARS = 16_000
_SUMMARY_PART_MAX_CHARS = 400


class ChatError(Exception):
    """会話の操作を断った。`code` だけを持ち、利用者の入力や LLM の出力を含めない。"""

    def __init__(self, code: ChatErrorCode) -> None:
        super().__init__(code)
        self.code: ChatErrorCode = code


class ChatBudgetPort(Protocol):
    async def reserve(
        self,
        today: date,
        model: str,
        worst_usd: Decimal,
        *,
        workspace: str,
        uid: str,
        purpose: ChatPurpose,
    ) -> tuple[ChatReservation | None, BudgetRefusal | None]: ...

    async def settle(
        self, reservation: ChatReservation, input_tokens: int, output_tokens: int
    ) -> None: ...


def _utc_today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class ChatDeps:
    session_factory: async_sessionmaker[AsyncSession]
    llm: TextLlm
    budget: ChatBudgetPort
    cost_guard: CostGuard
    today: Callable[[], date] = field(default=_utc_today)


# ------------------------------------------------------------------ 監査（C9）
async def record_chat_event(
    session: AsyncSession,
    *,
    actor: str,
    action: ChatAuditAction,
    workspace: str,
    chat_id: str | None,
    outcome: Literal["created", "deleted", "done", "failed", "purged"],
    message_count: int,
    error: ChatErrorCode | None = None,
) -> None:
    """会話の監査。残すのは chat_id・件数・成否（と固定のエラーコード）だけ。本文・題は残さない。"""
    detail: dict[str, Any] = {"chat_id": chat_id, "message_count": message_count}
    if error is not None:
        detail["error"] = error
    await AuditRepository(session).record(
        actor, action, f"{workspace}/chat/{chat_id or '*'}", outcome, detail
    )


# ------------------------------------------------------------------ CRUD
def _chat_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise ChatError("chat_not_found") from e


def _clean_title(title: str) -> str:
    t = " ".join(title.split())
    if len(t) > CHAT_TITLE_MAX_CHARS:
        raise ChatError("invalid_request")
    return t


async def _checked_binding(
    session: AsyncSession, workspace: str, kind: KnowledgeChatKind, artifact_id: str
) -> uuid.UUID:
    """結び付ける artifact が同じワークスペース・同じ種類か。規約の会話は結び付けない。"""
    if kind == "policy":
        raise ChatError("invalid_binding")
    try:
        aid = uuid.UUID(artifact_id)
    except ValueError as e:
        raise ChatError("invalid_binding") from e
    artifact = await ArtifactRepository(session).get(aid)
    if artifact is None or artifact.workspace != workspace or artifact.kind != kind:
        raise ChatError("invalid_binding")
    return aid


async def create_chat(
    session: AsyncSession,
    *,
    workspace: str,
    owner: str,
    kind: KnowledgeChatKind,
    artifact_id: str | None = None,
    title: str = "",
) -> KnowledgeChat:
    aid = await _checked_binding(session, workspace, kind, artifact_id) if artifact_id else None
    chat = await KnowledgeChatRepository(session).create(
        workspace=workspace, owner_uid=owner, kind=kind, artifact_id=aid, title=_clean_title(title)
    )
    await record_chat_event(
        session,
        actor=owner,
        action="knowledge.chat.create",
        workspace=workspace,
        chat_id=chat.id,
        outcome="created",
        message_count=0,
    )
    return chat


async def list_chats(
    session: AsyncSession,
    *,
    workspace: str,
    owner: str,
    kind: KnowledgeChatKind | None = None,
    artifact_id: str | None = None,
) -> list[KnowledgeChatSummary]:
    aid: uuid.UUID | None = None
    if artifact_id is not None:
        try:
            aid = uuid.UUID(artifact_id)
        except ValueError:
            return []
    return await KnowledgeChatRepository(session).list_for_owner(
        workspace, owner, kind=kind, artifact_id=aid
    )


async def get_chat(
    session: AsyncSession, *, workspace: str, owner: str, chat_id: str
) -> KnowledgeChat:
    chat = await KnowledgeChatRepository(session).get(workspace, owner, _chat_id(chat_id))
    if chat is None:
        raise ChatError("chat_not_found")
    return chat


async def delete_chat(session: AsyncSession, *, workspace: str, owner: str, chat_id: str) -> None:
    repo = KnowledgeChatRepository(session)
    cid = _chat_id(chat_id)
    chat = await repo.get(workspace, owner, cid, for_update=True)
    if chat is None or not await repo.delete(workspace, owner, cid):
        raise ChatError("chat_not_found")
    await record_chat_event(
        session,
        actor=owner,
        action="knowledge.chat.delete",
        workspace=workspace,
        chat_id=chat.id,
        outcome="deleted",
        message_count=len(chat.messages),
    )


async def patch_chat(
    session: AsyncSession,
    *,
    workspace: str,
    owner: str,
    chat_id: str,
    expected_version: int,
    title: str | None = None,
    artifact_id: str | None = None,
) -> KnowledgeChat:
    """題の変更と artifact への結び付け（新規作成の会話を、最初の保存の後に結ぶ）。"""
    repo = KnowledgeChatRepository(session)
    cid = _chat_id(chat_id)
    chat = await repo.get(workspace, owner, cid)
    if chat is None:
        raise ChatError("chat_not_found")
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = _clean_title(title)
    if artifact_id is not None:
        values["artifact_id"] = await _checked_binding(session, workspace, chat.kind, artifact_id)
    updated = await repo.update(workspace, owner, cid, expected_version=expected_version, **values)
    if updated is None:
        raise ChatError("version_conflict")
    return updated


async def purge_member_chats(session: AsyncSession, *, workspace: str, uid: str, actor: str) -> int:
    """メンバーを外したとき、その人の会話を消す（本人にも owner にも読めないデータを残さない）。"""
    n = await KnowledgeChatRepository(session).delete_for_owner(workspace, uid)
    if n:
        await record_chat_event(
            session,
            actor=actor,
            action="knowledge.chat.purge",
            workspace=workspace,
            chat_id=None,
            outcome="purged",
            message_count=n,
        )
    return n


# ------------------------------------------------------------------ 発言（C8）
def _size(messages: list[ChatMessage]) -> int:
    return sum(len(m.model_dump_json(exclude_none=True).encode()) for m in messages)


def _now() -> datetime:
    return datetime.now(UTC)


def _derive_title(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return first[:60] or "新しい会話"


def _is_stale(m: ChatMessage) -> bool:
    return m.status == "pending" and _now() - m.created_at > PENDING_STALE_AFTER


def _replay(chat: KnowledgeChat, key: str) -> ChatTurn | None:
    """同じ冪等キーの発言があれば、その結果（LLM を呼ばない）。処理中なら `message_pending`。"""
    user = next((m for m in chat.messages if m.role == "user" and m.idempotency_key == key), None)
    if user is None:
        return None
    if user.status == "pending" and not _is_stale(user):
        raise ChatError("message_pending")
    reply = next(
        (m for m in chat.messages if m.role == "assistant" and m.idempotency_key == key), None
    )
    return ChatTurn(chat=chat, user_message_id=user.id, reply=reply, replayed=True)


@dataclass(frozen=True)
class _Context:
    chat: KnowledgeChat
    message_id: str
    names: list[str]
    spec_domain: DomainSpec | None
    spec_legacy: list[SpecIR]


async def _spec_context(
    session: AsyncSession, chat: KnowledgeChat
) -> tuple[DomainSpec | None, list[SpecIR]]:
    """結び付いた仕様の今の domain と旧形式の IR（提案の診断に使う。本文は編集欄のものを使う）。"""
    if chat.kind != "spec" or chat.artifact_id is None:
        return None, []
    repo = ArtifactRepository(session)
    aid = uuid.UUID(chat.artifact_id)
    rev = await repo.current_draft(aid) or await repo.latest(aid)
    if rev is None or not isinstance(rev.content, SpecContent):
        return None, []
    return rev.content.domain, list(rev.content.legacy_ir)


async def _begin_turn(
    deps: ChatDeps, *, workspace: str, owner: str, cid: uuid.UUID, text: str, key: str
) -> _Context | ChatTurn:
    """保留の発言を足す（短いトランザクション）。同じキーの再送なら前の結果を返す。"""
    async with deps.session_factory() as s, s.begin():
        repo = KnowledgeChatRepository(s)
        chat = await repo.get(workspace, owner, cid, for_update=True)
        if chat is None:
            raise ChatError("chat_not_found")
        replayed = _replay(chat, key)
        if replayed is not None:
            return replayed
        user = ChatMessage(
            id=uuid.uuid4().hex,
            role="user",
            text=text,
            idempotency_key=key,
            status="pending",
            created_at=_now(),
        )
        if (
            len(chat.messages) + 2 > CHAT_MAX_MESSAGES
            or _size([*chat.messages, user]) + REPLY_MAX_BYTES > CHAT_MAX_BYTES
        ):
            raise ChatError("chat_full")
        names = [
            f"{a.kind}:{a.name}" for a in await ArtifactRepository(s).list_for_workspace(workspace)
        ]
        domain, legacy = await _spec_context(s, chat)
        await repo.save_messages(
            workspace, owner, cid, [*chat.messages, user], title=chat.title or _derive_title(text)
        )
    return _Context(chat, user.id, names, domain, legacy)


async def _finish_turn(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    cid: uuid.UUID,
    message_id: str,
    reply: ChatMessage | None,
    error: ChatErrorCode | None = None,
) -> ChatTurn | None:
    """持ち主と保留の発言を確かめてから確定する。会話・発言が消えていれば何もせず None。"""
    async with deps.session_factory() as s, s.begin():
        repo = KnowledgeChatRepository(s)
        chat = await repo.get(workspace, owner, cid, for_update=True)
        if chat is None:
            return None
        idx = next((i for i, m in enumerate(chat.messages) if m.id == message_id), None)
        if idx is None or chat.messages[idx].status != "pending":
            return None
        messages = list(chat.messages)
        status: Literal["done", "failed"] = "done" if reply is not None else "failed"
        messages[idx] = messages[idx].model_copy(update={"status": status})
        if reply is not None:
            messages.append(reply)
        saved = await repo.save_messages(workspace, owner, cid, messages)
        assert saved is not None
        await record_chat_event(
            s,
            actor=owner,
            action="knowledge.chat.message",
            workspace=workspace,
            chat_id=saved.id,
            outcome=status,
            message_count=len(messages),
            error=error,
        )
    return ChatTurn(chat=saved, user_message_id=message_id, reply=reply)


async def _settle_quietly(
    budget: ChatBudgetPort, reservation: ChatReservation, gen: JsonGeneration | None
) -> None:
    """精算。失敗しても元の結果を上書きしない（予約は最悪の額のまま残る＝多めに数える）。"""
    try:
        await budget.settle(
            reservation, gen.input_tokens if gen else 0, gen.output_tokens if gen else 0
        )
    except Exception as e:  # noqa: BLE001 - 型名だけ残す（C9）
        logger.warning("会話の費用の精算に失敗した: %s", type(e).__name__)


async def _call_llm(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    prompt: str,
    schema: dict[str, Any],
    purpose: ChatPurpose,
) -> str:
    """予約 → LLM → 精算。断ったとき・失敗したときは `ChatError`（固定のコード）。"""
    worst = deps.cost_guard.estimate(deps.llm.model, len(prompt.encode()), CHAT_OUTPUT_TOKENS)
    reservation, refusal = await deps.budget.reserve(
        deps.today(), deps.llm.model, worst, workspace=workspace, uid=owner, purpose=purpose
    )
    if reservation is None:
        raise ChatError(refusal or "cost_limit")
    gen: JsonGeneration | None = None
    try:
        async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
            gen = await deps.llm.generate_json(prompt, schema)
    except Exception as e:
        # 事業者の例外の文はプロンプトや入力を含みうるので、型名だけを残し、連鎖もさせない
        logger.warning("会話の AI の呼出しに失敗した: %s", type(e).__name__)
        raise ChatError("chat_failed") from None
    finally:
        await asyncio.shield(_settle_quietly(deps.budget, reservation, gen))
    return gen.text


def _parse_reply(raw: str) -> tuple[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ChatError("chat_failed") from None
    if not isinstance(data, dict):
        raise ChatError("chat_failed")
    explanation = data.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ChatError("chat_failed")
    return explanation.strip()[:_EXPLANATION_MAX_CHARS], data.get("proposal")


async def post_message(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    chat_id: str,
    text: str,
    idempotency_key: str,
    editor_text: str = "",
) -> ChatTurn:
    """発言を足し、AI の返答（説明＋提案）を保存して返す（C8）。"""
    if not text.strip() or len(text.encode()) > MESSAGE_MAX_BYTES:
        raise ChatError("invalid_request")
    if len(editor_text.encode()) > EDITOR_TEXT_MAX_BYTES:
        raise ChatError("input_too_large")
    cid = _chat_id(chat_id)
    begun = await _begin_turn(
        deps, workspace=workspace, owner=owner, cid=cid, text=text, key=idempotency_key
    )
    if isinstance(begun, ChatTurn):
        return begun
    ctx = begun

    async def fail(code: ChatErrorCode) -> ChatError:
        await asyncio.shield(
            _finish_turn(
                deps,
                workspace=workspace,
                owner=owner,
                cid=cid,
                message_id=ctx.message_id,
                reply=None,
                error=code,
            )
        )
        return ChatError(code)

    try:
        prompt = build_chat_prompt(
            ctx.chat.kind,
            message=text,
            editor_text=editor_text,
            names=ctx.names,
            history=ctx.chat.messages,
        )
        raw = await _call_llm(
            deps,
            workspace=workspace,
            owner=owner,
            prompt=prompt,
            schema=response_schema(ctx.chat.kind),
            purpose="chat_message",
        )
        explanation, proposal_data = _parse_reply(raw)
        proposal: ChatProposal | None = await build_proposal(
            ctx.chat.kind,
            proposal_data,
            editor_text=editor_text,
            base_hash=text_hash(editor_text),
            spec_domain=ctx.spec_domain,
            spec_legacy=ctx.spec_legacy,
        )
    except PromptTooLarge:
        raise await fail("input_too_large") from None
    except ChatError as e:
        raise await fail(e.code) from None
    except BaseException:
        # 取り消しなど。保留のまま残さない
        await fail("chat_failed")
        raise
    reply = ChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        text=explanation,
        proposal=proposal,
        idempotency_key=idempotency_key,
        status="done",
        created_at=_now(),
    )
    if len(reply.model_dump_json().encode()) > REPLY_MAX_BYTES:
        raise await fail("chat_failed")
    turn = await _finish_turn(
        deps,
        workspace=workspace,
        owner=owner,
        cid=cid,
        message_id=ctx.message_id,
        reply=reply,
    )
    if turn is None:  # 途中で会話が消された: 応答を捨て、行を復活させない
        raise ChatError("chat_not_found")
    return turn


# ------------------------------------------------------------------ 要約（C3）
def _compose_note(what: str, why: str, dropped: str) -> str:
    note = f"何を: {what}\nなぜ: {why}\n見送ったこと: {dropped}"
    return note if len(note) <= NOTE_MAX_CHARS else note[: NOTE_MAX_CHARS - 1] + "…"


async def summarize(
    deps: ChatDeps, *, workspace: str, owner: str, chat_id: str
) -> ChatSummaryDraft:
    """会話の要約の案（保存しない）。本人が確かめて直してから改訂の note に入れる。"""
    async with deps.session_factory() as s:
        chat = await get_chat(s, workspace=workspace, owner=owner, chat_id=chat_id)
    if not any(m.role == "user" and m.status == "done" for m in chat.messages):
        raise ChatError("nothing_to_summarize")
    raw = await _call_llm(
        deps,
        workspace=workspace,
        owner=owner,
        prompt=build_summary_prompt(chat.kind, chat.messages),
        schema=SUMMARY_SCHEMA,
        purpose="chat_summary",
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ChatError("chat_failed") from None
    parts = [data.get(k) if isinstance(data, dict) else None for k in ("what", "why", "dropped")]
    if not all(isinstance(p, str) for p in parts):
        raise ChatError("chat_failed")
    what, why, dropped = (str(p).strip()[:_SUMMARY_PART_MAX_CHARS] for p in parts)
    return ChatSummaryDraft(
        what=what, why=why, dropped=dropped, note=_compose_note(what, why, dropped)
    )


__all__ = [
    "CHAT_OUTPUT_TOKENS",
    "ChatBudgetPort",
    "ChatDeps",
    "ChatError",
    "create_chat",
    "delete_chat",
    "get_chat",
    "list_chats",
    "patch_chat",
    "post_message",
    "purge_member_chats",
    "record_chat_event",
    "summarize",
]
