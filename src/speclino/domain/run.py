"""run-manifest（PoC 5.3 / 12.3）。入力ハッシュと版を固定し、再現可能にする。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.common import SCHEMA_VERSION, IRModel

GateStatus = Literal["pass", "pass_with_exceptions", "fail"]
ExecState = Literal[
    "queued", "running", "awaiting_answer", "completed", "failed", "cancelled", "superseded"
]


class RunInputs(IRModel):
    repository: str | None = None
    head_commit: str = Field(min_length=1)
    base_commit: str | None = None
    spec_content_hash: str | None = None
    spec_ir_hash: str | None = None
    bindings_hash: str | None = None
    domain_hash: str | None = None
    contracts_hash: str | None = None
    policy_hash: str | None = None
    code_ir_hash: str | None = Field(default=None, description="--code-ir 入力時のハッシュ")


class RunVersions(IRModel):
    speclino: str
    analyzer: str
    engine: str
    solver: str
    settings_hash: str | None = None


class RunManifest(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    created_at: datetime
    inputs: RunInputs
    versions: RunVersions
    limits: AnalysisLimits
    fail_on: list[str] = Field(default_factory=list, description="ゲート失敗にする verdict")
    mode: Literal["advisory", "blocking"] = "advisory"
