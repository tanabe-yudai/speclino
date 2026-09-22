"""IR の型定義（ロジックを置かない）。"""

from speclino.domain.bindings import BehaviorBinding, Bindings, OperationBinding, OperationContract
from speclino.domain.code_ir import (
    AnalysisLimits,
    CodeIR,
    Event,
    Outcome,
    Path,
    StateUpdate,
    UnknownCause,
)
from speclino.domain.common import (
    SCHEMA_VERSION,
    ClaimScope,
    IRModel,
    Precision,
    SnapshotRef,
    SourceRef,
)
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Binary, Const, Expr, Nary, Not, Ref
from speclino.domain.finding import (
    Category,
    Counterexample,
    CoverageCounts,
    Finding,
    Findings,
    Severity,
    Verdict,
    VerdictCounts,
)
from speclino.domain.run import ExecState, GateStatus, RunInputs, RunManifest, RunVersions
from speclino.domain.spec_ir import (
    ApprovalRecord,
    Assertion,
    CountConstraint,
    EventRequirement,
    ExpressionAssertion,
    Rule,
    SpecIR,
)
from speclino.domain.trace import TraceRecord

__all__ = [
    "SCHEMA_VERSION",
    "AnalysisLimits",
    "ApprovalRecord",
    "Assertion",
    "BehaviorBinding",
    "Binary",
    "Bindings",
    "Category",
    "ClaimScope",
    "CodeIR",
    "Const",
    "CountConstraint",
    "Counterexample",
    "CoverageCounts",
    "DomainSpec",
    "Event",
    "EventRequirement",
    "ExecState",
    "Expr",
    "ExpressionAssertion",
    "Finding",
    "Findings",
    "GateStatus",
    "IRModel",
    "Nary",
    "Not",
    "OperationBinding",
    "OperationContract",
    "Outcome",
    "Path",
    "Precision",
    "Ref",
    "Rule",
    "RunInputs",
    "RunManifest",
    "RunVersions",
    "Severity",
    "SnapshotRef",
    "SourceRef",
    "SpecIR",
    "StateUpdate",
    "TraceRecord",
    "UnknownCause",
    "Verdict",
    "VerdictCounts",
]
