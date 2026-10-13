"""テスト実行トレースの最小契約。観測は静的判定と別軸で保持する。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from speclino.domain.common import SCHEMA_VERSION, IRModel
from speclino.domain.spec_ir import EventKind, EventPhase

Origin = Literal["static_analysis", "static_test_call_analysis", "runtime_trace"]


class TraceEvent(IRModel):
    sequence: int = Field(ge=1)
    kind: EventKind
    phase: EventPhase
    operation: str | None = None
    target: str | None = None
    arguments: dict[str, bool | int | str] = Field(default_factory=dict)
    value: bool | int | str | None = None
    source: str = Field(min_length=1, description="計測したソースまたはアダプターの識別子")


class TraceOutcome(IRModel):
    kind: Literal["http_response", "return", "exception", "unresolved"]
    status: int | None = None
    exception_type: str | None = None


class Completeness(IRModel):
    missing_events: bool = Field(description="トレースの欠落があるか")
    instrumented_kinds: list[EventKind] = Field(default_factory=list)
    terminated_normally: bool = Field(description="正常に終了記録を取得したか")


class TraceRecord(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    test_id: str = Field(min_length=1)
    code_commit: str = Field(min_length=1)
    behavior_id: str = Field(min_length=1)
    origin: Origin = "runtime_trace"
    before: dict[str, bool | int | str] = Field(default_factory=dict)
    events: list[TraceEvent] = Field(default_factory=list)
    after: dict[str, bool | int | str] = Field(default_factory=dict)
    outcome: TraceOutcome
    mocked: list[str] = Field(default_factory=list, description="モックに置き換えた操作 ID")
    observation_scope: list[str] = Field(default_factory=list)
    completeness: Completeness
