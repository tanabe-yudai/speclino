"""A2A のタスクの実行: 入力の解釈・前回の状態の読み戻し・スキルへの振り分け。判定はしない。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message
from google.protobuf.json_format import MessageToDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.a2a_server.auth import IDENTITY_STATE_KEY
from speclino.agent.consultant.request import SkillRequestError, parse_skill_request
from speclino.agent.consultant.tools import persist_audit
from speclino.agent.gateway import InMemoryAudit
from speclino.domain.a2a import SkillId, SkillRequest, TokenIdentity

logger = logging.getLogger(__name__)

# 入力待ちにするときの質問の metadata の鍵（2 回目以降に SkillCall.state として読み戻す）
STATE_METADATA_KEY = "speclino"


@dataclass
class SkillCall:
    """スキルの処理に渡すもの。並びは後続のスキルが位置引数で使うので変えない。"""

    request: SkillRequest
    identity: TokenIdentity
    updater: TaskUpdater
    task_id: str
    reply: str | None  # 2 回目以降の本文（1 回目は None）
    state: dict[str, Any] | None  # 前回の質問の metadata["speclino"]（1 回目は None）
    audit: InMemoryAudit  # 門番の記録。executor が finally で DB に残す


SkillHandler = Callable[[SkillCall], Awaitable[None]]


class SkillsUnavailable(RuntimeError):
    """スキルを組み立てられない（費用ガードが無い等）。メッセージは相手に見せてよい文。"""


class SpecLinoExecutor(AgentExecutor):
    """入力の解釈・状態の読み戻し・スキルへの振り分け。判定はしない。

    handlers と session_factory は app の state から引くため、関数で受け取って実行のたびに呼ぶ
    （`create_app` の時点では state がまだ無い）。
    """

    def __init__(
        self,
        get_handlers: Callable[[], dict[SkillId, SkillHandler]],
        get_session_factory: Callable[[], async_sessionmaker[AsyncSession]],
    ) -> None:
        self.get_handlers = get_handlers
        self.get_session_factory = get_session_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ValueError("A2A のメッセージが無い")
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        identity: TokenIdentity = context.call_context.state[IDENTITY_STATE_KEY]
        first = task.history[0] if task.history else message
        try:
            request = parse_skill_request(first)
        except SkillRequestError as e:
            await updater.failed(updater.new_agent_message([new_text_part(str(e))]))
            return
        try:
            handlers = self.get_handlers()
        except SkillsUnavailable as e:  # 計量なしで動かさず、理由を返して失敗にする
            await updater.failed(updater.new_agent_message([new_text_part(str(e))]))
            return
        except Exception as e:  # 組み立ての失敗も理由つきの failed にする（SDK に投げない）
            logger.exception("a2a skills could not be built")
            await updater.failed(
                updater.new_agent_message(
                    [new_text_part(f"処理に失敗しました: {type(e).__name__}")]
                )
            )
            return
        handler = handlers.get(request.skill)
        if handler is None:
            await updater.failed(
                updater.new_agent_message([new_text_part(f"{request.skill} は使えません")])
            )
            return
        resumed = first.message_id != message.message_id  # 2 回目以降
        state = _speclino_state(task.status.message) if resumed else None
        reply = context.get_user_input() if resumed else None
        audit = InMemoryAudit()
        try:
            await handler(SkillCall(request, identity, updater, task.id, reply, state, audit))
        except Exception as e:  # 失敗の理由を返し、途中の結果を判定のように見せない
            logger.exception("a2a skill failed")
            await updater.failed(
                updater.new_agent_message(
                    [new_text_part(f"処理に失敗しました: {type(e).__name__}")]
                )
            )
        finally:
            # 入力待ち・失敗・例外のどの抜け方でも、門番の拒否を含む監査を DB に残す
            await self.persist(identity, audit, task.id)

    async def persist(self, identity: TokenIdentity, audit: InMemoryAudit, task_id: str) -> None:
        await persist_audit(self.get_session_factory(), identity, audit, task_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _speclino_state(message: Message) -> dict[str, Any]:
    """前回の質問（task.status.message）の metadata["speclino"]。無ければ空。"""
    if not message.HasField("metadata"):
        return {}
    value = _restore_ints(MessageToDict(message.metadata).get(STATE_METADATA_KEY))
    return value if isinstance(value, dict) else {}


def _restore_ints(value: Any) -> Any:
    """protobuf の Struct は数を double で持つ（1 → 1.0）。整数だった値を int に戻す。"""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _restore_ints(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore_ints(v) for v in value]
    return value
