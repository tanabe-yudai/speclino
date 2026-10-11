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
- 調べてから答える（ストリームの発言、`ChatDeps.research`）: 読むだけの関数で承認済みの知識・
  登録済みのリポジトリ・Web を調べてから最後の JSON を流す（services/knowledge_chat_research.py）。
  予約はターンの予算で 1 回、出典はサーバが組み立てて返答に残し、監査には関数の種類と回数だけ
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
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
    SEARCH_SUGGESTION_MAX_BYTES,
    SEARCH_SUGGESTIONS_MAX,
    ChatAuditAction,
    ChatErrorCode,
    ChatMessage,
    ChatProposal,
    ChatStreamEvent,
    ChatSummaryDraft,
    ChatTurn,
    KnowledgeChat,
    KnowledgeChatKind,
    KnowledgeChatSummary,
)
from speclino.domain.spec_ir import SpecIR
from speclino.ports.text_llm import (
    JsonGeneration,
    JsonStreamEvent,
    StreamingTextLlm,
    TextLlm,
    Thought,
    ToolStreamingLlm,
)
from speclino.services.cost_guard import CostGuard
from speclino.services.draft_stream import DraftSnapshots
from speclino.services.json_string_stream import JsonStringStream
from speclino.services.knowledge_chat_budget import BudgetRefusal, ChatPurpose, ChatReservation
from speclino.services.knowledge_chat_prompt import (
    SUMMARY_SCHEMA,
    PromptTooLarge,
    build_chat_prompt,
    build_summary_prompt,
)
from speclino.services.knowledge_chat_proposal import (
    build_proposal,
    draft_text_field,
    response_schema,
    text_hash,
)
from speclino.services.knowledge_chat_research import (
    ChatResearch,
    ResearchDone,
    ResearchEvent,
    ToolActivity,
    research,
    turn_budget,
)
from speclino.services.knowledge_chat_tools import ChatTools

logger = logging.getLogger(__name__)

# 取り消されても最後までやり切る後始末のタスク。asyncio はタスクを弱く参照するだけなので、
# 待ち手が取り消されたあとに回収されないよう、終わるまでここで持つ
_DETACHED: set[asyncio.Task[Any]] = set()


async def _finish_detached(coro: Any) -> Any:
    """待ち手が取り消されても、`coro` は別タスクで最後まで走らせる。"""
    task = asyncio.ensure_future(coro)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    return await asyncio.shield(task)


LLM_TIMEOUT_SECONDS = 120.0
# 会話の 1 回の出力トークンの上限（設計 C7、思考を含む。費用の予約もこの値で見積もる）。
# 思考は LOW にして、枠を本文に残す（api/routes/knowledge_chat.py の配線）
CHAT_OUTPUT_TOKENS = 16384
# 出力の上限で返答が切れたとき、提案を省いて説明だけで確定し、利用者にこう伝える
TRUNCATED_NOTICE = "本文が長いため提案を省きました。範囲を絞って頼んでください"
# これより古い保留の発言は、処理が途中で止まったものとして扱う（再送を永久に断らない）
PENDING_STALE_AFTER = timedelta(minutes=10)
_EXPLANATION_MAX_CHARS = 16_000
_SUMMARY_PART_MAX_CHARS = 400
# 調べた返答に足す出典と検索の候補の表示の大きさの上界（会話の大きさの見積もりに足す）
_RESEARCH_EXTRA_MAX_BYTES = SEARCH_SUGGESTIONS_MAX * SEARCH_SUGGESTION_MAX_BYTES + 32 * 1024


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
        self,
        reservation: ChatReservation,
        input_tokens: int,
        output_tokens: int,
        extra_usd: Decimal = Decimal("0"),
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
    # 調べてから答える配線。None か、LLM が関数の呼出しに対応しなければ従来どおり 1 回で答える
    research: ChatResearch | None = None


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
    tools: dict[str, int] | None = None,
) -> None:
    """会話の監査。残すのは chat_id・件数・成否（と固定のエラーコード）だけ。本文・題は残さない。

    `tools` は調べた関数の種類ごとの回数（引数・結果の中身は残さない）。
    """
    detail: dict[str, Any] = {"chat_id": chat_id, "message_count": message_count}
    if error is not None:
        detail["error"] = error
    if tools:
        detail["tools"] = dict(sorted(tools.items()))
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
    if user.status == "pending":
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
        if any(_is_stale(m) for m in chat.messages):
            # 処理が途中で止まった発言（10 分を過ぎた保留）は失敗に直してから進める
            fixed = [
                m.model_copy(update={"status": "failed"}) if _is_stale(m) else m
                for m in chat.messages
            ]
            saved = await repo.save_messages(workspace, owner, cid, fixed)
            assert saved is not None
            chat = saved
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
            or _size([*chat.messages, user]) + REPLY_MAX_BYTES + _RESEARCH_EXTRA_MAX_BYTES
            > CHAT_MAX_BYTES
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
    tool_counts: dict[str, int] | None = None,
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
            tools=tool_counts,
        )
    return ChatTurn(chat=saved, user_message_id=message_id, reply=reply)


async def _settle_quietly(
    budget: ChatBudgetPort,
    reservation: ChatReservation,
    gen: JsonGeneration | None,
    extra_usd: Decimal = Decimal("0"),
) -> None:
    """精算。失敗しても元の結果を上書きしない（予約は最悪の額のまま残る＝多めに数える）。"""
    try:
        await budget.settle(
            reservation,
            gen.input_tokens if gen else 0,
            gen.output_tokens if gen else 0,
            extra_usd,
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
) -> JsonGeneration:
    """予約 → LLM → 精算。断ったとき・失敗したときは `ChatError`（固定のコード）。

    精算: 成功は実費。事業者の例外は 0（課金されない前提）。タイムアウト・取り消しは
    LLM が処理を続けて課金されうるので、予約した最悪の額のまま残す（過少に数えない）。
    """
    worst = deps.cost_guard.estimate(deps.llm.model, len(prompt.encode()), CHAT_OUTPUT_TOKENS)
    reservation, refusal = await deps.budget.reserve(
        deps.today(), deps.llm.model, worst, workspace=workspace, uid=owner, purpose=purpose
    )
    if reservation is None:
        raise ChatError(refusal or "cost_limit")
    gen: JsonGeneration | None = None
    keep_worst = False
    try:
        async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
            gen = await deps.llm.generate_json(prompt, schema)
    except TimeoutError:
        keep_worst = True
        logger.warning("会話の AI の呼出しが時間切れになった")
        raise ChatError("chat_failed") from None
    except asyncio.CancelledError:
        keep_worst = True
        raise
    except Exception as e:
        # 事業者の例外の文はプロンプトや入力を含みうるので、型名だけを残し、連鎖もさせない
        logger.warning("会話の AI の呼出しに失敗した: %s", type(e).__name__)
        raise ChatError("chat_failed") from None
    finally:
        if not keep_worst:  # 最悪の額のまま残すときは精算の行を足さない
            await asyncio.shield(_settle_quietly(deps.budget, reservation, gen))
    return gen


def _cut_off(raw: str, err: json.JSONDecodeError) -> bool:
    """JSON が途中で切れている（壊れているのではなく、末尾まで読んで足りない）。"""
    body = raw.rstrip()
    # 文字列の途中で終わった（Unterminated string）か、末尾で次の値・区切りを待っている
    return body.startswith("{") and (
        err.msg.startswith("Unterminated string") or err.pos >= len(body) - 1
    )


def _partial_explanation(raw: str) -> str:
    """切れた応答から、最後まで書かれた説明だけを取り出す（無ければ空）。"""
    m = re.search(r'"explanation"\s*:\s*', raw)
    if m is None:
        return ""
    try:
        value, _ = json.JSONDecoder().raw_decode(raw, m.end())
    except json.JSONDecodeError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _truncated_reply(raw: str) -> tuple[str, Any]:
    """出力の上限で切れた返答: 提案を省き、説明（あれば）と知らせだけで確定する。"""
    explanation = _partial_explanation(raw)[:_EXPLANATION_MAX_CHARS]
    return (f"{explanation}\n\n{TRUNCATED_NOTICE}" if explanation else TRUNCATED_NOTICE), None


def _parse_reply(gen: JsonGeneration) -> tuple[str, Any]:
    raw = gen.text
    if gen.truncated:
        return _truncated_reply(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if _cut_off(raw, e):
            return _truncated_reply(raw)
        raise ChatError("chat_failed") from None
    if not isinstance(data, dict):
        raise ChatError("chat_failed")
    explanation = data.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ChatError("chat_failed")
    return explanation.strip()[:_EXPLANATION_MAX_CHARS], data.get("proposal")


def _check_input(text: str, editor_text: str) -> None:
    if not text.strip():
        raise ChatError("invalid_request")
    if len(text.encode()) > MESSAGE_MAX_BYTES:
        raise ChatError("message_too_long")
    if len(editor_text.encode()) > EDITOR_TEXT_MAX_BYTES:
        raise ChatError("input_too_large")


async def _fail_turn(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    cid: uuid.UUID,
    ctx: _Context,
    code: ChatErrorCode,
) -> ChatError:
    """保留の発言を失敗に直す（取り消されても終わらせる）。"""
    await _finish_detached(
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


async def _complete_turn(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    cid: uuid.UUID,
    ctx: _Context,
    gen: JsonGeneration,
    key: str,
    editor_text: str,
    researched: ResearchDone | None = None,
) -> ChatTurn:
    """LLM の応答を検証して返答を確定する（提案の検証は最後に 1 度だけ）。

    調べた返答には、サーバが組み立てた出典と検索の候補の表示を添える。
    """
    explanation, proposal_data = _parse_reply(gen)
    proposal: ChatProposal | None = await build_proposal(
        ctx.chat.kind,
        proposal_data,
        editor_text=editor_text,
        base_hash=text_hash(editor_text),
        spec_domain=ctx.spec_domain,
        spec_legacy=ctx.spec_legacy,
        propose_name=_proposes_name(ctx),
    )
    reply = ChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        text=explanation,
        proposal=proposal,
        idempotency_key=key,
        status="done",
        created_at=_now(),
        sources=(researched.sources or None) if researched else None,
        search_suggestions=(researched.search_suggestions or None) if researched else None,
    )
    extras = {"sources", "search_suggestions"}
    if len(reply.model_dump_json(exclude=extras).encode()) > REPLY_MAX_BYTES:
        raise ChatError("chat_failed")
    # 確定は取り消されても終わらせる（保留のまま残さない）
    turn = await asyncio.shield(
        _finish_turn(
            deps,
            workspace=workspace,
            owner=owner,
            cid=cid,
            message_id=ctx.message_id,
            reply=reply,
            tool_counts=researched.tool_counts if researched else None,
        )
    )
    if turn is None:  # 途中で会話が消された: 応答を捨て、行を復活させない
        raise ChatError("chat_not_found")
    return turn


def _proposes_name(ctx: _Context) -> bool:
    """新規作成の会話（まだ知識に結び付かない。規約を除く）では、名前の案も出させる（C4）。"""
    return ctx.chat.kind != "policy" and ctx.chat.artifact_id is None


def _chat_prompt(ctx: _Context, text: str, editor_text: str, tools: ChatTools | None = None) -> str:
    return build_chat_prompt(
        ctx.chat.kind,
        message=text,
        editor_text=editor_text,
        names=ctx.names,
        history=ctx.chat.messages,
        propose_name=_proposes_name(ctx),
        research_repos=(
            [f"{r.full_name}（{r.default_branch}）" for r in tools.repos]
            if tools is not None
            else None
        ),
    )


async def _chat_tools(deps: ChatDeps, workspace: str) -> ChatTools | None:
    """調べるターンの関数。LLM が関数の呼出しに対応しなければ None（従来どおり）。"""
    if deps.research is None or not isinstance(deps.llm, ToolStreamingLlm):
        return None
    return await ChatTools.load(
        deps.session_factory,
        workspace,
        reader=deps.research.reader,
        searcher=deps.research.searcher,
    )


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
    _check_input(text, editor_text)
    cid = _chat_id(chat_id)
    begun = await _begin_turn(
        deps, workspace=workspace, owner=owner, cid=cid, text=text, key=idempotency_key
    )
    if isinstance(begun, ChatTurn):
        return begun
    ctx = begun

    async def fail(code: ChatErrorCode) -> ChatError:
        return await _fail_turn(deps, workspace=workspace, owner=owner, cid=cid, ctx=ctx, code=code)

    try:
        gen = await _call_llm(
            deps,
            workspace=workspace,
            owner=owner,
            prompt=_chat_prompt(ctx, text, editor_text),
            schema=response_schema(ctx.chat.kind, propose_name=_proposes_name(ctx)),
            purpose="chat_message",
        )
        return await _complete_turn(
            deps,
            workspace=workspace,
            owner=owner,
            cid=cid,
            ctx=ctx,
            gen=gen,
            key=idempotency_key,
            editor_text=editor_text,
        )
    except PromptTooLarge:
        raise await fail("input_too_large") from None
    except ChatError as e:
        raise await fail(e.code) from None
    except BaseException:
        # 取り消しなど。保留のまま残さない
        await fail("chat_failed")
        raise


async def _llm_events(
    llm: TextLlm, prompt: str, schema: dict[str, Any]
) -> AsyncIterator[JsonStreamEvent]:
    """ストリームに対応しない LLM は、全文を 1 つの最終要素として返す。"""
    if hasattr(llm, "stream_json"):
        streaming: StreamingTextLlm = llm  # type: ignore[assignment]
        async for event in streaming.stream_json(prompt, schema):
            yield event
    else:
        yield await llm.generate_json(prompt, schema)


async def _close_quietly(events: AsyncIterator[Any]) -> None:
    aclose = getattr(events, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception as e:  # noqa: BLE001 - 型名だけ残す（C9）
            logger.warning("会話の AI のストリームを閉じられなかった: %s", type(e).__name__)


def _research_events(
    deps: ChatDeps, tools: ChatTools, prompt: str, schema: dict[str, Any]
) -> AsyncIterator[ResearchEvent]:
    assert deps.research is not None and isinstance(deps.llm, ToolStreamingLlm)
    llm: ToolStreamingLlm = deps.llm
    return research(
        llm.open(prompt),
        tools,
        llm=llm,
        guard=deps.cost_guard,
        searcher=deps.research.searcher,
        schema=schema,
        prompt_bytes=len(prompt.encode()),
        limits=deps.research.limits,
    )


async def _stream_llm(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    prompt: str,
    schema: dict[str, Any],
    purpose: ChatPurpose,
    tools: ChatTools | None = None,
) -> AsyncIterator[JsonStreamEvent | ResearchEvent]:
    """`_call_llm` のストリーム版。断片（str）を流し、最後に JsonGeneration を 1 つ流す。

    精算は `_call_llm` と同じ規則: 最後まで読み切ったときだけ実費で精算する。事業者の例外は、
    断片を受け取る前なら 0、受け取った後なら最悪の額のまま残す。
    時間切れ・取り消し・呼び手が読むのをやめた（切断）・最終要素が来なかった場合は、使用量が
    分からない（LLM は処理を続けて課金されうる）ので、予約した最悪の額のまま残す。

    `tools` を渡すと調べるターンになる: 予約はターンの予算（`turn_budget`）で 1 回、途中で
    思考の要約（Thought）と調べていること（ToolActivity）も流し、最後は `ResearchDone`
    （トークンの合計と検索の費用で精算する）。調べる歩は始めた時点で課金されうるので、
    失敗したら最悪の額のまま残す。
    """
    researching = (
        tools is not None and deps.research is not None and isinstance(deps.llm, ToolStreamingLlm)
    )
    if researching:
        assert deps.research is not None and isinstance(deps.llm, ToolStreamingLlm)
        worst = turn_budget(
            deps.cost_guard,
            deps.llm,
            deps.research.searcher,
            len(prompt.encode()),
            deps.research.limits,
        )
        timeout = LLM_TIMEOUT_SECONDS + deps.research.limits.seconds
    else:
        worst = deps.cost_guard.estimate(deps.llm.model, len(prompt.encode()), CHAT_OUTPUT_TOKENS)
        timeout = LLM_TIMEOUT_SECONDS
    reservation, refusal = await deps.budget.reserve(
        deps.today(), deps.llm.model, worst, workspace=workspace, uid=owner, purpose=purpose
    )
    if reservation is None:
        raise ChatError(refusal or "cost_limit")
    gen: JsonGeneration | None = None
    done: ResearchDone | None = None
    keep_worst = False
    # 断片を 1 つでも受け取った = 事業者側で課金が始まっている（調べるターンは最初から）
    received = researching
    events: AsyncIterator[JsonStreamEvent | ResearchEvent] = (
        _research_events(deps, tools, prompt, schema)
        if researching and tools is not None
        else _llm_events(deps.llm, prompt, schema)
    )
    # 全体の期限。yield の間（呼び手の処理中）に時間切れが割り込まないよう、1 歩ずつ区切って測る
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    event = await anext(events)
            except StopAsyncIteration:
                break
            if isinstance(event, ResearchDone):
                done, gen = event, event.gen
            elif isinstance(event, JsonGeneration):
                gen = event
            else:
                received = True
                yield event
        if gen is None:  # 最終要素なしで終わった: 使用量が分からない
            keep_worst = True
            raise ChatError("chat_failed")
    except TimeoutError:
        keep_worst = True
        logger.warning("会話の AI の呼出しが時間切れになった")
        raise ChatError("chat_failed") from None
    except (asyncio.CancelledError, GeneratorExit):
        keep_worst = True
        raise
    except ChatError:
        raise
    except Exception as e:
        # 途中まで出力した後の失敗は使用量が分からないので最悪の額を残す。出力前なら 0 で精算する
        keep_worst = received
        # 事業者の例外の文はプロンプトや入力を含みうるので、型名だけを残し、連鎖もさせない
        logger.warning("会話の AI の呼出しに失敗した: %s", type(e).__name__)
        raise ChatError("chat_failed") from None
    finally:
        if not keep_worst:  # 最悪の額のまま残すときは精算の行を足さない
            extra = done.extra_usd if done is not None else Decimal("0")
            await _finish_detached(_settle_quietly(deps.budget, reservation, gen, extra))
        await _close_quietly(events)
    yield done if done is not None else gen


async def stream_message(
    deps: ChatDeps,
    *,
    workspace: str,
    owner: str,
    chat_id: str,
    text: str,
    idempotency_key: str,
    editor_text: str = "",
) -> AsyncIterator[ChatStreamEvent]:
    """`post_message` のストリーム版。出来事を順に返し、最後は `done` か `error` のどちらか。

    保存・冪等・費用の扱いは `post_message` と同じ部品を使う。取り消し（切断）されたら、
    保留の発言を失敗にして最悪の額の予約を残し、例外をそのまま投げ直す。
    """
    try:
        _check_input(text, editor_text)
        cid = _chat_id(chat_id)
        begun = await _begin_turn(
            deps, workspace=workspace, owner=owner, cid=cid, text=text, key=idempotency_key
        )
    except ChatError as e:
        yield ChatStreamEvent(type="error", code=e.code)
        return
    if isinstance(begun, ChatTurn):
        yield ChatStreamEvent(type="done", turn=begun)
        return
    ctx = begun

    async def fail(code: ChatErrorCode) -> ChatError:
        return await _fail_turn(deps, workspace=workspace, owner=owner, cid=cid, ctx=ctx, code=code)

    yield ChatStreamEvent(type="status", state="thinking")
    explanation_stream = JsonStringStream("explanation")
    draft_field = draft_text_field(ctx.chat.kind)
    drafts = DraftSnapshots("proposal", draft_field) if draft_field else None
    gen: JsonGeneration | None = None
    researched: ResearchDone | None = None
    try:
        tools = await _chat_tools(deps, workspace)
        async for item in _stream_llm(
            deps,
            workspace=workspace,
            owner=owner,
            prompt=_chat_prompt(ctx, text, editor_text, tools),
            schema=response_schema(ctx.chat.kind, propose_name=_proposes_name(ctx)),
            purpose="chat_message",
            tools=tools,
        ):
            if isinstance(item, ResearchDone):
                researched, gen = item, item.gen
            elif isinstance(item, JsonGeneration):
                gen = item
            elif isinstance(item, Thought):
                yield ChatStreamEvent(type="thought", text=item.text)
            elif isinstance(item, ToolActivity):
                yield ChatStreamEvent(type="tool", text=item.text)
            else:
                if piece := explanation_stream.feed(item):
                    yield ChatStreamEvent(type="delta", text=piece)
                if drafts and draft_field and (snap := drafts.feed(item)) is not None:
                    yield ChatStreamEvent(type="draft", field=draft_field, text=snap)
        if gen is None:
            raise ChatError("chat_failed")
        if drafts and draft_field and (snap := drafts.finish()) is not None:
            yield ChatStreamEvent(type="draft", field=draft_field, text=snap)
        turn = await _complete_turn(
            deps,
            workspace=workspace,
            owner=owner,
            cid=cid,
            ctx=ctx,
            gen=gen,
            key=idempotency_key,
            editor_text=editor_text,
            researched=researched,
        )
    except PromptTooLarge:
        err = await fail("input_too_large")
        yield ChatStreamEvent(type="error", code=err.code)
        return
    except ChatError as e:
        err = await fail(e.code)
        yield ChatStreamEvent(type="error", code=err.code)
        return
    except BaseException:
        # 取り消し・切断。保留の発言を失敗に直す（予約は `_stream_llm` が最悪の額のまま残す）
        await fail("chat_failed")
        raise
    yield ChatStreamEvent(type="done", turn=turn)


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
    gen = await _call_llm(
        deps,
        workspace=workspace,
        owner=owner,
        prompt=build_summary_prompt(chat.kind, chat.messages),
        schema=SUMMARY_SCHEMA,
        purpose="chat_summary",
    )
    raw = gen.text
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
    "stream_message",
    "summarize",
]
