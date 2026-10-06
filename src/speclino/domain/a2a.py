"""A2A の議論役（S59、design/2026-10-04-a2a-spec-agent.md）の型。ロジックを書かない。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from speclino.domain.run import GitRef

SkillId = Literal["spec_review", "requirement_consult", "run_clarification", "spec_suggest"]
SKILL_IDS: tuple[SkillId, ...] = (
    "spec_review",
    "requirement_consult",
    "run_clarification",
    "spec_suggest",
)


class SkillRequest(BaseModel):
    """最初のメッセージから読んだスキルの指定（DataPart か front matter）。"""

    model_config = ConfigDict(extra="forbid")

    skill: SkillId
    repo: str | None = None
    behavior: str | None = None
    path: str | None = None
    requirement_id: str | None = None
    commit: GitRef | None = None
    run_id: uuid.UUID | None = None
    text: str = ""  # 本文（仕様の Markdown または質問）


class TokenIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    team: str
    token_id: uuid.UUID


class AgentSupplementView(BaseModel):
    """検査の詳細に出す補足。正式な回答ではない。"""

    id: uuid.UUID
    requirement_id: str | None
    question: str
    content: str
    created_at: datetime
    source: str = Field(description="どのエージェント（トークン名）から来たか")


class CostReservation(BaseModel):
    """日次の費用枠に先に書いた最悪の費用。精算は同じ日・同じモデルで行う。"""

    model_config = ConfigDict(frozen=True)

    day: date
    model: str
    worst_usd: Decimal
