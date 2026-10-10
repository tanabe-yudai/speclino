"""対話による知識の作成（S73、design/2026-10-06-knowledge-chat.md）の型。ロジックは持たない。

会話は持ち主だけが読み書きする（C2）。AI の返答は説明の文と下書きの提案で、提案は種類ごとの
許可リストの欄だけを持つ（C4）。提案は元の本文のハッシュ（`base_hash`）に結び付き、サーバの
診断（C5）を添える。診断は保存・承認の検証とは別のもの。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from speclino.domain.common import IRModel

KnowledgeChatKind = Literal["spec", "skill", "instructions", "policy"]
ChatRole = Literal["user", "assistant"]
ChatMessageStatus = Literal["pending", "done", "failed"]
DiagnosticSeverity = Literal["error", "warning", "info"]
NoteSource = Literal["manual", "chat_summary"]
# 会話の API が返す固定のエラーコード（C9: 入力・LLM の応答・例外の文を返さない）
ChatErrorCode = Literal[
    "chat_not_found",
    "invalid_request",
    "message_too_long",
    "invalid_binding",
    "version_conflict",
    "chat_full",
    "message_pending",
    "input_too_large",
    "message_limit",
    "cost_limit",
    "chat_unavailable",
    "chat_failed",
    "nothing_to_summarize",
]
ChatAuditAction = Literal[
    "knowledge.chat.create",
    "knowledge.chat.delete",
    "knowledge.chat.message",
    "knowledge.chat.purge",
]

CHAT_TITLE_MAX_CHARS = 120
CHAT_MAX_MESSAGES = 200
CHAT_MAX_BYTES = 512 * 1024
MESSAGE_MAX_BYTES = 8 * 1024
EDITOR_TEXT_MAX_BYTES = 64 * 1024
REPLY_MAX_BYTES = 64 * 1024
NOTE_MAX_CHARS = 500
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class ProposalDiagnostic(IRModel):
    """提案の診断 1 件（仕様は `compile_spec`、ほかは型と検査器の一覧）。"""

    severity: DiagnosticSeverity
    code: str = Field(min_length=1)
    message: str


class ChatProposal(IRModel):
    """下書きの提案。`raw_proposal` は許可リストの欄だけ（型に合わなくても返す）。

    新規作成の会話（規約を除く）では、規則に合う名前の案 `name` が本文の欄に添うことがある（C4）。
    """

    base_hash: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$", description="提案の土台にした編集欄の本文のハッシュ"
    )
    raw_proposal: dict[str, Any]
    diagnostics: list[ProposalDiagnostic] = Field(default_factory=list)
    valid: bool = Field(description="診断にエラーが無いか（保存・承認の検証は別に行う）")


class ChatMessage(IRModel):
    id: str = Field(min_length=1)
    role: ChatRole
    text: str
    proposal: ChatProposal | None = None
    idempotency_key: str | None = None
    status: ChatMessageStatus
    created_at: datetime


class KnowledgeChat(IRModel):
    id: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    owner_uid: str = Field(min_length=1)
    kind: KnowledgeChatKind
    artifact_id: str | None = None
    title: str = Field(max_length=CHAT_TITLE_MAX_CHARS)
    messages: list[ChatMessage] = Field(default_factory=list)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class KnowledgeChatSummary(IRModel):
    """一覧の 1 行（本文を含めない）。"""

    id: str
    kind: KnowledgeChatKind
    artifact_id: str | None = None
    title: str
    message_count: int = Field(ge=0)
    version: int = Field(ge=1)
    updated_at: datetime


class ChatTurn(IRModel):
    """発言の結果。`replayed` は同じ冪等キーの再送で、LLM を呼ばずに前の結果を返したこと。"""

    chat: KnowledgeChat
    user_message_id: str
    reply: ChatMessage | None = None
    replayed: bool = False


ChatStreamEventType = Literal["status", "delta", "draft", "done", "error"]


class ChatStreamEvent(IRModel):
    """ストリームで返す出来事。`delta` は説明の増えた分（足していく）、`done` は確定した結果。

    `draft` は提案の本文の途中経過で、`text` は増分ではなくここまでの全文（置き換える）。
    表示専用で、確定値は `done` の提案から取る。

    `status` は進行の段階（`state`）、`error` は固定のエラーコード（`code`）だけを持つ（C9）。
    """

    type: ChatStreamEventType
    state: Literal["thinking"] | None = None
    text: str | None = None
    field: str | None = None
    turn: ChatTurn | None = None
    code: ChatErrorCode | None = None


class ChatSummaryDraft(IRModel):
    """会話の要約の案（保存しない）。`note` は本人が直して改訂の説明に入れる文。"""

    what: str
    why: str
    dropped: str
    note: str = Field(max_length=NOTE_MAX_CHARS)


__all__ = [
    "ChatAuditAction",
    "ChatErrorCode",
    "CHAT_MAX_BYTES",
    "CHAT_MAX_MESSAGES",
    "CHAT_TITLE_MAX_CHARS",
    "EDITOR_TEXT_MAX_BYTES",
    "IDEMPOTENCY_KEY_PATTERN",
    "MESSAGE_MAX_BYTES",
    "NOTE_MAX_CHARS",
    "REPLY_MAX_BYTES",
    "ChatMessage",
    "ChatMessageStatus",
    "ChatProposal",
    "ChatRole",
    "ChatStreamEvent",
    "ChatStreamEventType",
    "ChatSummaryDraft",
    "ChatTurn",
    "DiagnosticSeverity",
    "KnowledgeChat",
    "KnowledgeChatKind",
    "KnowledgeChatSummary",
    "NoteSource",
    "ProposalDiagnostic",
]
