"""run-manifest（PoC 5.3 / 12.3）。入力ハッシュと版を固定し、再現可能にする。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.code_ir import AnalysisLimits, ReachabilityDigest
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


class BehaviorAnalysis(IRModel):
    """増分解析（S41c）の記録。再利用は「解析していない」ことなので必ず記録して隠さない。"""

    behavior_id: str = Field(min_length=1)
    reachability: ReachabilityDigest = Field(
        description="到達関数群のソースハッシュ（未解決呼出しも含め隠さず記録する）"
    )
    inputs_hash: str = Field(
        min_length=1, description="仕様・domain・bindings・contracts の内容ハッシュ"
    )
    reused_from_base: bool = Field(description="base の Code IR を再利用したか")
    reason: str = Field(min_length=1, description="再利用した／しなかった理由（人が読める説明）")


class RunManifest(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    created_at: datetime
    inputs: RunInputs
    versions: RunVersions
    limits: AnalysisLimits
    fail_on: list[str] = Field(default_factory=list, description="ゲート失敗にする verdict")
    mode: Literal["advisory", "blocking"] = "advisory"
    analysis: list[BehaviorAnalysis] = Field(
        default_factory=list, description="behavior ごとの増分解析の記録（S41c）"
    )
    policy_team: str | None = Field(
        default=None, description="適用したチーム規約の team 識別子（S42）"
    )
    policy_revision: int | None = Field(
        default=None,
        description="適用したチーム規約の改訂番号（S42）。replay はこの改訂で再評価する",
    )
