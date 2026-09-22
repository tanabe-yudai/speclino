"""`questions` テーブル: human_ask の構造化質問と回答（FR 015）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Question, QuestionStatus


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        run_id: uuid.UUID,
        requirement_id: str | None,
        head_sha: str,
        text: str,
        options: dict[str, Any],
    ) -> Question:
        q = Question(
            run_id=run_id,
            requirement_id=requirement_id,
            head_sha=head_sha,
            text=text,
            options=options,
        )
        self._session.add(q)
        await self._session.flush()
        return q

    async def get(self, question_id: uuid.UUID) -> Question | None:
        return await self._session.get(Question, question_id)

    async def find_by_short_id(self, short: str) -> Question | None:
        """`Q-<先頭8桁>` で引く。"""
        stmt = select(Question).where(Question.status == "open")
        for q in (await self._session.execute(stmt)).scalars().all():
            if str(q.id).startswith(short.lower()):
                return q
        return None

    async def list_for_run(
        self, run_id: uuid.UUID, status: QuestionStatus | None = None
    ) -> list[Question]:
        stmt = select(Question).where(Question.run_id == run_id)
        if status is not None:
            stmt = stmt.where(Question.status == status)
        return list(
            (await self._session.execute(stmt.order_by(Question.created_at))).scalars().all()
        )

    async def set_answer(
        self, question_id: uuid.UUID, answer: dict[str, Any], status: QuestionStatus = "answered"
    ) -> None:
        await self._session.execute(
            update(Question).where(Question.id == question_id).values(answer=answer, status=status)
        )
