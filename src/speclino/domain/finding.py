"""Finding（PoC 13.3）と評価結果。severity と verdict は別にする。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from speclino.domain.common import SCHEMA_VERSION, ClaimScope, IRModel, SourceRef

Verdict = Literal["VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW"]

Category = Literal[
    "missing_effect",
    "forbidden_effect",
    "constraint_mismatch",
    "state_mismatch",
    "outcome_mismatch",
    "argument_mismatch",
    "cardinality_mismatch",
    "undocumented_behavior",
    "unsupported_analysis",
    "ambiguous_spec",
    "stale_approval",
    "vacuous_applicability",
    "potential_violation",
    "solver_limit",
    "conformant",
    "missing_finding",
]

Severity = Literal["high", "medium", "low", "info"]
BaseHeadStatus = Literal["new", "existing", "resolved", "unchanged"]
TestObservation = Literal["OBSERVED", "NOT_OBSERVED", "CONTRADICTED"]


class Counterexample(IRModel):
    kind: Literal["symbolic", "observed"]
    path_id: str | None = None
    assignments: dict[str, bool | int | str] = Field(
        default_factory=dict, description="変数（名前空間つき）→ 値"
    )
    description: str = Field(min_length=1)


class Finding(IRModel):
    id: str = Field(min_length=1, description="決定的に生成する安定 ID")
    requirement_id: str = Field(min_length=1)
    behavior_id: str = Field(min_length=1)
    verdict: Verdict
    category: Category
    severity: Severity
    message: str = Field(min_length=1)
    expected: str | None = None
    actual: str | None = None
    counterexample: Counterexample | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    claim_scope: ClaimScope
    analysis_scope: str | None = Field(default=None, description="検査した経路と対応モデルの範囲")
    code_refs: list[SourceRef] = Field(default_factory=list)
    spec_refs: list[SourceRef] = Field(default_factory=list)
    base_head_status: BaseHeadStatus | None = None
    test_observation: TestObservation | None = Field(
        default=None, description="動的観測は静的 verdict と別軸"
    )
    next_steps: list[str] = Field(
        default_factory=list, description="未検証を検証可能にする具体策（UNKNOWN / REVIEW 用）"
    )
    assessment_outcome: Literal["issue", "no_issue", "split"] | None = Field(
        default=None, description="LLM 支援の規約だけ。verdict は常に REVIEW"
    )


class VerdictCounts(IRModel):
    VERIFIED_IN_SCOPE: int = 0
    VIOLATED: int = 0
    UNKNOWN: int = 0
    REVIEW: int = 0


class CoverageCounts(IRModel):
    """一致率だけを出さないための併記項目（PoC 17.3）。"""

    requirements_total: int = 0
    requirements_approved: int = 0
    requirements_in_scope: int = 0
    requirements_decided: int = 0
    requirements_unanalyzed: int = 0


class Findings(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    counts: VerdictCounts = Field(default_factory=VerdictCounts)
    coverage: CoverageCounts = Field(default_factory=CoverageCounts)
