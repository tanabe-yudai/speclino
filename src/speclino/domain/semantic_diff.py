"""意味差分。base / head それぞれの適合結果と Code IR を突き合わせた分類。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from speclino.domain.common import SCHEMA_VERSION, IRModel, Precision
from speclino.domain.finding import Verdict

FindingChange = Literal[
    "new_violation",  # head で VIOLATED、base では違反でない（または要求が無い）
    "existing_violation",  # base / head とも VIOLATED
    "resolved_violation",  # base で VIOLATED、head で VERIFIED_IN_SCOPE
    "became_undecidable",  # base で判定済み、head で UNKNOWN / REVIEW
    "became_decidable",  # base で UNKNOWN / REVIEW、head で VERIFIED_IN_SCOPE
    "added",  # base に対応する判定が無く、head は違反でない
    "removed",  # base にだけ判定がある
    "unchanged",  # verdict が同じ
    "changed",  # 上記以外の遷移（例: REVIEW → UNKNOWN）
]
RuleChange = Literal["added", "changed", "removed", "unchanged"]
BehaviorChange = Literal["added", "changed", "removed", "unchanged"]


class FindingDelta(IRModel):
    requirement_id: str = Field(min_length=1)
    behavior_id: str = Field(min_length=1)
    base_verdict: Verdict | None = None
    head_verdict: Verdict | None = None
    status: FindingChange


class RuleDelta(IRModel):
    rule_id: str = Field(min_length=1)
    behavior_id: str = Field(min_length=1)
    status: RuleChange


class PathSummary(IRModel):
    """経路の内容を人が読める形に圧縮したもの（差分の表示用）。"""

    path_id: str = Field(min_length=1)
    guard: str
    events: list[str] = Field(default_factory=list)
    outcome: str
    precision: Precision


class BehaviorDelta(IRModel):
    behavior_id: str = Field(min_length=1)
    status: BehaviorChange
    added_paths: list[PathSummary] = Field(default_factory=list)
    removed_paths: list[PathSummary] = Field(default_factory=list)
    unchanged_paths: int = 0


class SemanticDiffCounts(IRModel):
    new_violation: int = 0
    existing_violation: int = 0
    resolved_violation: int = 0
    became_undecidable: int = 0
    became_decidable: int = 0
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    changed: int = 0


class SemanticDiff(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    base_commit: str | None = None
    head_commit: str = Field(min_length=1)
    base_available: bool = Field(
        description="base 側の判定が得られたか。False なら差分は head だけから作る（全て added）",
    )
    base_error: str | None = Field(
        default=None, description="base 側を判定できなかった理由（仕様が無い等）"
    )
    findings: list[FindingDelta] = Field(default_factory=list)
    rules: list[RuleDelta] = Field(default_factory=list)
    behaviors: list[BehaviorDelta] = Field(default_factory=list)
    counts: SemanticDiffCounts = Field(default_factory=SemanticDiffCounts)
