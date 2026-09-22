"""Code IR（PoC 6.3 / 6.7）。解析した実装の振る舞いを経路ごとに保持する。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from speclino.domain.common import SCHEMA_VERSION, IRModel, Precision, SnapshotRef, SourceRef
from speclino.domain.expr import Expr
from speclino.domain.spec_ir import EventKind, EventPhase

Certainty = Literal["must", "may"]

UnknownCode = Literal[
    "unsupported_syntax",
    "unresolved_call",
    "missing_contract",
    "unresolved_attribute",
    "limit_depth",
    "limit_paths",
    "limit_time",
    "unmodeled_outcome",
    "other",
]


class UnknownCause(IRModel):
    code: UnknownCode
    detail: str = Field(min_length=1)
    source_ref: SourceRef | None = None
    affects: list[str] = Field(
        default_factory=list, description="影響を受ける要求 ID（分かる場合）"
    )


class StateUpdate(IRModel):
    target: str = Field(min_length=1, description="更新対象の変数名（名前空間なし）")
    value: Expr
    source_ref: SourceRef | None = None


class Event(IRModel):
    sequence: int = Field(ge=1)
    kind: EventKind
    phase: EventPhase
    operation: str | None = Field(default=None, description="call のときの業務操作 ID")
    target: str | None = Field(default=None, description="write のときの更新対象")
    arguments: dict[str, Expr] = Field(default_factory=dict)
    value: Expr | None = None
    certainty: Certainty = "must"
    source_ref: SourceRef | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> Event:
        if self.kind == "call" and not self.operation:
            raise ValueError("call イベントには operation が必要")
        if self.kind == "write" and not self.target:
            raise ValueError("write イベントには target が必要")
        return self

    @property
    def operation_or_target(self) -> str:
        return (self.operation or self.target) or ""


class Outcome(IRModel):
    kind: Literal["http_response", "return", "exception", "unresolved"]
    status: int | None = Field(default=None, ge=100, le=599)
    value: Expr | None = None
    exception_type: str | None = None
    source_ref: SourceRef | None = None


class Path(IRModel):
    path_id: str = Field(min_length=1)
    guard: Expr = Field(description="この経路を通る入口条件（before 名前空間）")
    state_updates: list[StateUpdate] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    outcome: Outcome
    precision: Precision
    unknown_causes: list[UnknownCause] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_events_ordered(self) -> Path:
        seqs = [e.sequence for e in self.events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            raise ValueError("events.sequence は昇順かつ一意")
        if self.precision == "incomplete" and not self.unknown_causes:
            raise ValueError("precision=incomplete には unknown_causes が必要")
        return self


class AnalysisLimits(IRModel):
    depth: int = Field(ge=0, description="直接呼出しを展開する深さ")
    max_paths: int = Field(ge=1)
    solver_timeout_ms: int = Field(ge=1)


class CodeIR(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    behavior_id: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1, description="完全修飾シンボル")
    snapshot: SnapshotRef
    analyzer_version: str = Field(min_length=1)
    limits: AnalysisLimits
    paths: list[Path] = Field(default_factory=list)
    unknown_causes: list[UnknownCause] = Field(
        default_factory=list, description="経路に紐づかない振る舞い全体の未解決要因"
    )
    origin: Literal["static_analysis", "handwritten"] = "static_analysis"

    @property
    def complete(self) -> bool:
        """全経路が exact_in_model で未解決要因が無い。"""
        return not self.unknown_causes and all(
            p.precision == "exact_in_model" and not p.unknown_causes for p in self.paths
        )
