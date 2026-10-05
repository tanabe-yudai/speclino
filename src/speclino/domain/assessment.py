"""LLM 支援の規約の評価記録と人の確定（S70、設計 6.2–6.3）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.common import IRModel

AssessmentOutcome = Literal["issue", "no_issue", "split"]
Disposition = Literal["attested", "acknowledged_violation"]


class AssessmentKey(IRModel):
    policy_revision: int = Field(ge=1)
    rule_id: str = Field(min_length=1)
    unit_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)

    def as_str(self) -> str:
        parts = (
            self.policy_revision,
            self.rule_id,
            self.unit_hash,
            self.model,
            self.prompt_version,
        )
        return "|".join(str(x) for x in parts)


class ItemVerdict(IRModel):
    item_id: str = Field(min_length=1)
    outcome: AssessmentOutcome
    votes: list[bool] = Field(min_length=1, description="試行ごとの「該当する」")
    quote: str | None = None


class AssessmentRecord(IRModel):
    key: AssessmentKey
    unit_symbol: str = Field(min_length=1)
    items: list[ItemVerdict]
    created_at: datetime


class AssessmentUnit(IRModel):
    """LLM 支援で評価する単位（関数・文字列リテラル・エンドポイント）。"""

    symbol: str = Field(
        min_length=1, description="例 src/app/api.py::create_order または ...::L42:'msg'"
    )
    kind: Literal["function", "string_literal", "endpoint"]
    text: str = Field(min_length=1, description="正規化前の本文（根拠の引用に使う）")
    unit_hash: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$", description="空白とコメントを除いた本文の hash"
    )


class Attestation(IRModel):
    fingerprint: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    disposition: Disposition
    actor: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    # 確定の属するプロジェクト（#114）。欄の無い古い manifest も読めるよう既定は None
    project_id: str | None = None
    created_at: datetime
    self_attested: bool | None = Field(
        default=None,
        description=(
            "PR の作成者が自分で確定したか（GitHub のユーザー ID で照合。拒否はしないが印を残す）。"
            "None は照合できない（確定者が GitHub 未連携、または PR の作成者の ID が無い run）"
        ),
    )


__all__ = [
    "AssessmentKey",
    "AssessmentOutcome",
    "AssessmentUnit",
    "AssessmentRecord",
    "Attestation",
    "Disposition",
    "ItemVerdict",
]
