"""対話による知識の作成（S73）の API。判断は services/knowledge_chat.py。

- ワークスペースのメンバーだけが使える（別のワークスペース・存在しないワークスペースは 404）。
  会話は持ち主だけのもので、他人の会話の ID は owner / approver でも 404（`chat_not_found`）
- エラーは固定のコードと固定の文だけを返す（C9）。リクエストの body は自前で読んで検証し、
  pydantic の検証の詳細（入力を含みうる）を返さない。予期しない失敗も型名だけをログに残す
- 費用ガードが無い構成では LLM を呼ばない（503、フェイルクローズ）
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, NoReturn, get_args

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from speclino.adapters.llm.chat_scripted import ScriptedChatLlm
from speclino.adapters.llm.gemini_search import GeminiSearch
from speclino.adapters.llm.gemini_tools import GeminiToolLlm
from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.knowledge_chat import (
    CHAT_TITLE_MAX_CHARS,
    IDEMPOTENCY_KEY_PATTERN,
    ChatErrorCode,
    ChatStreamEvent,
    KnowledgeChatKind,
)
from speclino.domain.workspace import AuthUser
from speclino.ports.text_llm import TextLlm
from speclino.services import knowledge_chat as kc
from speclino.services.knowledge_chat_budget import ChatBudget
from speclino.services.knowledge_chat_research import ChatResearch
from speclino.services.workspaces import WorkspaceNotFound, require_member

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workspaces/{slug}/knowledge/chats",
    tags=["knowledge"],
    responses=error_responses(401, 404),
)

# JSON の文字のエスケープで膨らむ分を見込んだ body の上限（本文 64KB ＋発言 8KB）
MAX_BODY_BYTES = 256 * 1024

ERRORS: dict[ChatErrorCode, tuple[int, str]] = {
    "chat_not_found": (404, "会話が見つかりません"),
    "invalid_request": (422, "リクエストの形式が正しくありません"),
    "message_too_long": (413, "発言が長すぎます（8KB まで）"),
    "invalid_binding": (422, "この会話に結び付けられない知識です"),
    "version_conflict": (409, "会話が別の画面で変わりました。読み直してください"),
    "chat_full": (409, "この会話はいっぱいです。新しい会話を始めてください"),
    "message_pending": (409, "この発言はまだ処理中です"),
    "input_too_large": (413, "入力が大きすぎます"),
    "message_limit": (429, "今日の発言の上限に達しました"),
    "cost_limit": (429, "AI の利用枠の上限に達しました。時間をおいて試してください"),
    "chat_unavailable": (503, "AI との会話を今は使えません"),
    "chat_failed": (502, "AI の応答を得られませんでした。もう一度試してください"),
    "nothing_to_summarize": (422, "要約する会話がまだありません"),
}
_KINDS = set(get_args(KnowledgeChatKind))


class CreateChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeChatKind
    artifact_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="", max_length=CHAT_TITLE_MAX_CHARS)


class PatchChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=CHAT_TITLE_MAX_CHARS)
    artifact_id: str | None = Field(default=None, max_length=64)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 長さ（UTF-8 で 8KB）はサービスで数え、固定のコード message_too_long で断る
    text: str = Field(min_length=1)
    idempotency_key: str = Field(pattern=IDEMPOTENCY_KEY_PATTERN)
    editor_text: str = Field(default="", max_length=64 * 1024)


def _raise(code: ChatErrorCode) -> NoReturn:
    status, message = ERRORS[code]
    raise api_error(status, code, message) from None


def _unexpected(e: Exception) -> NoReturn:
    # 例外の文は SQL のパラメータ（会話の本文）を含みうるので、型名だけを残す
    logger.error("会話の API で予期しない失敗: %s", type(e).__name__)
    raise api_error(500, "internal_error", "処理に失敗しました") from None


async def _read_body[T: BaseModel](request: Request, model: type[T]) -> T:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        _raise("input_too_large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            _raise("input_too_large")
        chunks.append(chunk)
    try:
        return model.model_validate_json(b"".join(chunks))
    except ValidationError:
        _raise("invalid_request")


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """openapi の宣言（body は自前で読むので FastAPI には検証させない）。"""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


async def _member(state: AppState, slug: str, user: AuthUser) -> None:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
    except WorkspaceNotFound:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from None


_THINKING_LEVELS = {
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}


def _model(state: AppState) -> str:
    settings = state.settings
    return settings.chat_model or settings.a2a_model or settings.agent_model


def _llm(state: AppState) -> TextLlm:
    """会話の LLM。出力の上限は設計 C7 の 16,384 トークン（思考を含む）に固定する。

    Gemini は読むだけの関数で調べてから答える（`GeminiToolLlm`）。思考の量は設定
    `chat_thinking_level`（既定 medium）。
    """
    if state.knowledge_chat_llm is None:
        settings = state.settings
        if settings.chat_llm == "scripted":
            state.knowledge_chat_llm = ScriptedChatLlm()
        else:
            state.knowledge_chat_llm = GeminiToolLlm(
                _model(state),
                max_output_tokens=kc.CHAT_OUTPUT_TOKENS,
                thinking_level=_THINKING_LEVELS[settings.chat_thinking_level],
            )
    return state.knowledge_chat_llm


def _research(state: AppState) -> ChatResearch:
    """調べる配線。Web 検索は Gemini のときだけ（台本の LLM は関数を呼ばないので使われない）。"""
    if state.knowledge_chat_search is None and state.settings.chat_llm == "gemini":
        state.knowledge_chat_search = GeminiSearch(_model(state))
    return ChatResearch(reader=state.repo_reader, searcher=state.knowledge_chat_search)


def _deps(state: AppState) -> kc.ChatDeps:
    guard = state.service.cost_guard
    if guard is None:
        _raise("chat_unavailable")
    factory = state.session_factory
    return kc.ChatDeps(
        factory,
        _llm(state),
        ChatBudget(factory, guard),
        guard,
        model_ids=state.settings.llm_model_ids(),
        research=_research(state),
    )


@router.get("", responses=error_responses(422))
async def list_chats(
    slug: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> list[dict[str, Any]]:
    await _member(state, slug, user)
    kind = request.query_params.get("kind")
    if kind is not None and kind not in _KINDS:
        _raise("invalid_request")
    try:
        async with state.session_factory() as s:
            rows = await kc.list_chats(
                s,
                workspace=slug,
                owner=user.uid,
                kind=kind,  # type: ignore[arg-type]
                artifact_id=request.query_params.get("artifact_id"),
            )
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return [r.model_dump(mode="json") for r in rows]


@router.post(
    "",
    responses=error_responses(413, 422),
    openapi_extra=_body_schema(CreateChatRequest),
)
async def create_chat(
    slug: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    await _member(state, slug, user)
    body = await _read_body(request, CreateChatRequest)
    try:
        async with state.session_factory() as s, s.begin():
            # body を読む間に外されていないか、作るトランザクションの中で確かめ直す
            await require_member(s, slug, user.uid)
            chat = await kc.create_chat(
                s,
                workspace=slug,
                owner=user.uid,
                kind=body.kind,
                artifact_id=body.artifact_id,
                title=body.title,
            )
    except WorkspaceNotFound:
        raise api_error(404, "workspace_not_found", "ワークスペースが無い") from None
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return chat.model_dump(mode="json")


@router.get("/{chat_id}")
async def get_chat(
    slug: str,
    chat_id: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    await _member(state, slug, user)
    try:
        async with state.session_factory() as s:
            chat = await kc.get_chat(s, workspace=slug, owner=user.uid, chat_id=chat_id)
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return chat.model_dump(mode="json")


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    slug: str,
    chat_id: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> None:
    await _member(state, slug, user)
    try:
        async with state.session_factory() as s, s.begin():
            await kc.delete_chat(s, workspace=slug, owner=user.uid, chat_id=chat_id)
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)


@router.patch(
    "/{chat_id}",
    responses=error_responses(409, 413, 422),
    openapi_extra=_body_schema(PatchChatRequest),
)
async def patch_chat(
    slug: str,
    chat_id: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    await _member(state, slug, user)
    body = await _read_body(request, PatchChatRequest)
    try:
        async with state.session_factory() as s, s.begin():
            chat = await kc.patch_chat(
                s,
                workspace=slug,
                owner=user.uid,
                chat_id=chat_id,
                expected_version=body.expected_version,
                title=body.title,
                artifact_id=body.artifact_id,
            )
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return chat.model_dump(mode="json")


@router.post(
    "/{chat_id}/messages",
    responses=error_responses(409, 413, 422, 429, 502, 503),
    openapi_extra=_body_schema(MessageRequest),
)
async def post_message(
    slug: str,
    chat_id: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    await _member(state, slug, user)
    body = await _read_body(request, MessageRequest)
    deps = _deps(state)
    try:
        turn = await kc.post_message(
            deps,
            workspace=slug,
            owner=user.uid,
            chat_id=chat_id,
            text=body.text,
            idempotency_key=body.idempotency_key,
            editor_text=body.editor_text,
        )
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return turn.model_dump(mode="json")


# 待っている間に出す生存確認の間隔。プロキシ・LB が無通信の接続を切らないようにする
HEARTBEAT_SECONDS = 10.0
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _sse_event(ev: ChatStreamEvent) -> bytes:
    if ev.type == "status":
        return _sse("status", {"state": ev.state})
    if ev.type == "delta":
        return _sse("delta", {"text": ev.text})
    if ev.type == "draft":
        return _sse("draft", {"field": ev.field, "text": ev.text})
    if ev.type in ("thought", "tool"):
        return _sse(ev.type, {"text": ev.text})
    if ev.type == "done":
        assert ev.turn is not None
        return _sse("done", ev.turn.model_dump(mode="json"))
    status, message = ERRORS[ev.code or "chat_failed"]
    return _sse("error", {"code": ev.code, "message": message, "status": status})


async def _sse_stream(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[bytes]:
    """サービスの出来事を SSE にする。待っている間は生存確認のコメントを送る。

    サービスは別のタスクで動かす。切断（この生成器の取り消し・終了）ではそのタスクを取り消し、
    サービス側が費用の予約を最悪の額のまま残して発言を失敗にする（精算しない）。
    """
    queue: asyncio.Queue[ChatStreamEvent | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for ev in events:
                await queue.put(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 型名だけ残す（C9）
            logger.error("会話のストリームで予期しない失敗: %s", type(e).__name__)
            await queue.put(ChatStreamEvent(type="error", code="chat_failed"))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield b": ping\n\n"
                continue
            if item is None:
                break
            yield _sse_event(item)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post(
    "/{chat_id}/messages/stream",
    responses=error_responses(409, 413, 422, 429, 502, 503),
    openapi_extra=_body_schema(MessageRequest),
)
async def stream_message(
    slug: str,
    chat_id: str,
    request: Request,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> StreamingResponse:
    """post_message のストリーム版（text/event-stream）。

    event は status / delta（説明の増えた分）/ draft（提案の本文の途中）/ thought（思考の要約の
    増えた分）/ tool（調べていること）/ done（post_message と同じ ChatTurn）/ error。
    ストリームを始める前の失敗（認証・メンバー・body・LLM が無い）は通常の HTTP エラー。
    """
    await _member(state, slug, user)
    body = await _read_body(request, MessageRequest)
    deps = _deps(state)
    events = kc.stream_message(
        deps,
        workspace=slug,
        owner=user.uid,
        chat_id=chat_id,
        text=body.text,
        idempotency_key=body.idempotency_key,
        editor_text=body.editor_text,
    )
    return StreamingResponse(
        _sse_stream(events), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.post("/{chat_id}/summary", responses=error_responses(422, 429, 502, 503))
async def summarize(
    slug: str,
    chat_id: str,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    await _member(state, slug, user)
    deps = _deps(state)
    try:
        draft = await kc.summarize(deps, workspace=slug, owner=user.uid, chat_id=chat_id)
    except kc.ChatError as e:
        _raise(e.code)
    except Exception as e:  # noqa: BLE001
        _unexpected(e)
    return draft.model_dump(mode="json")
