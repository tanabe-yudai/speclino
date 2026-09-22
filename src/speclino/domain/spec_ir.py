"""Spec IR（PoC 6.2 / 6.5）。各 Rule を独立した判定単位とする。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from speclino.domain.common import SCHEMA_VERSION, ClaimScope, IRModel, SourceRef
from speclino.domain.expr import Expr

Modality = Literal["must", "must_not"]
RuleLevel = Literal["mandatory", "advisory"]
EventKind = Literal["call", "write", "return", "raise"]
EventPhase = Literal["invoked", "applied", "returned", "raised"]


class CountConstraint(IRModel):
    operator: Literal["eq", "ge", "le"]
    value: int = Field(ge=0)


class ExpressionAssertion(IRModel):
    """boolean Expr の真偽を検査する。終了状態や応答の比較に用いる。"""

    kind: Literal["expression"]
    expr: Expr


class EventRequirement(IRModel):
    """種別・操作・phase で選んだイベントの総数と、各イベントの引数を検査する。

    イベント総数は引数による絞り込みの前に数える。arguments_equal に指定した項目は、
    選択された全イベントで一致することを要求する。
    """

    kind: Literal["event_requirement"]
    event_kind: EventKind
    operation_or_target: str = Field(min_length=1, description="業務操作 ID または書込み対象")
    phase: EventPhase
    count: CountConstraint
    arguments_equal: dict[str, Expr] = Field(default_factory=dict)


Assertion = Annotated[ExpressionAssertion | EventRequirement, Field(discriminator="kind")]


class Rule(IRModel):
    id: str = Field(min_length=1, description="安定した要求／判定単位 ID（例 R-CANCEL-04）")
    behavior_id: str = Field(min_length=1)
    title: str | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    approval_ref: str | None = Field(
        default=None, description="この仕様解釈を承認した記録の ID。None は未承認"
    )
    modality: Modality
    level: RuleLevel = "mandatory"
    claim_scope: ClaimScope
    applicability: Expr = Field(description="この要求が適用される入口条件")
    assertion: Assertion


class ApprovalRecord(IRModel):
    """仕様解釈の承認記録。LLM が生成してはならない。"""

    id: str = Field(min_length=1)
    approver: str = Field(min_length=1, description="承認者の識別子（GitHub ログイン等）")
    approved_at: datetime
    source: Literal["merged_default_branch", "pr_comment", "manual"]
    spec_content_hash: str = Field(description="承認時の仕様本文ハッシュ（sha256:...）")
    ir_content_hash: str = Field(description="承認時の Spec IR ハッシュ（sha256:...）")
    evidence_url: str | None = None


class SpecIR(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    behavior_id: str = Field(min_length=1)
    spec_snapshot_id: str | None = Field(default=None, description="仕様スナップショットの ID")
    spec_content_hash: str | None = Field(default=None, description="仕様本文のハッシュ")
    rules: list[Rule] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    unextracted: list[str] = Field(
        default_factory=list,
        description="抽出しきれなかった要求 ID。対応できる要求だけを分母にしないために残す",
    )
