"""遷移（状態遷移図）の義務の検査（設計 5.1、手書き IR）。"""

from __future__ import annotations

from speclino.conformance.engine import EngineConfig
from speclino.conformance.state_check import check_state_machine
from speclino.domain import (
    AnalysisLimits,
    CodeIR,
    DomainSpec,
    Event,
    Outcome,
    Path,
    SnapshotRef,
    StateUpdate,
    UnknownCause,
)
from speclino.domain.common import Precision
from speclino.domain.expr import Expr, and_, const, eq, ne, not_, ref
from speclino.domain.finding import Finding
from speclino.domain.uml import StateMachine, StateTransition

DOMAIN = DomainSpec(
    enums={"OrderStatus": ["PENDING", "PAID", "SHIPPED", "CANCELLED"]},
    variables={"order.status": "OrderStatus", "order.paid": "boolean"},
)
CONFIG = EngineConfig(solver_timeout_ms=1000)
STATUS = ref("before.order.status")
TRUE = const("boolean", True)


def S(v: str) -> Expr:
    return const("OrderStatus", v)


def is_(v: str) -> Expr:
    return eq(STATUS, S(v))


def _sm(guarded: bool = False) -> StateMachine:
    paid = eq(ref("before.order.paid"), TRUE) if guarded else None
    return StateMachine(
        id="st-order#order.status",
        attribute="order.status",
        states=["PENDING", "PAID", "SHIPPED", "CANCELLED"],
        initial="PENDING",
        transitions=[
            StateTransition(id="t1", source="PENDING", target="PAID", event="pay", guard=paid),
            StateTransition(id="t2", source="PAID", target="SHIPPED", event="ship"),
            StateTransition(id="t3", source="PENDING", target="CANCELLED", event="cancel"),
            StateTransition(id="t4", source="PAID", target="CANCELLED", event="cancel"),
        ],
        level="mandatory",
    )


def _path(
    pid: str,
    guard: Expr,
    writes: list[str],
    precision: Precision = "exact_in_model",
    certainty: str = "must",
    causes: list[UnknownCause] | None = None,
) -> Path:
    return Path(
        path_id=pid,
        guard=guard,
        events=[
            Event.model_validate(
                {
                    "sequence": i + 1,
                    "kind": "write",
                    "phase": "applied",
                    "target": "order.status",
                    "value": S(w).model_dump(),
                    "certainty": certainty,
                }
            )
            for i, w in enumerate(writes)
        ],
        state_updates=[StateUpdate(target="order.status", value=S(w)) for w in writes],
        outcome=Outcome(kind="return"),
        precision=precision,
        unknown_causes=causes or [],
    )


def _rest(pid: str, guard: Expr) -> Path:
    return _path(pid, guard, [])


def _code(*paths: Path, causes: list[UnknownCause] | None = None) -> CodeIR:
    return CodeIR(
        behavior_id="pay_order",
        entrypoint="app.orders.OrderService.pay",
        snapshot=SnapshotRef(snapshot_id="test"),
        analyzer_version="test",
        limits=AnalysisLimits(depth=2, max_paths=64, solver_timeout_ms=1000),
        paths=list(paths),
        unknown_causes=causes or [],
    )


def _one(code: CodeIR, sm: StateMachine | None = None, domain: DomainSpec = DOMAIN) -> Finding:
    fs = check_state_machine(sm or _sm(), code, CONFIG, domain=domain)
    assert len(fs) == 1
    return fs[0]


def test_legal_transition_is_verified() -> None:
    f = _one(_code(_path("p1", is_("PENDING"), ["PAID"]), _rest("p2", ne(STATUS, S("PENDING")))))
    assert (f.verdict, f.category) == ("VERIFIED_IN_SCOPE", "conformant"), f.message


def test_illegal_state_transition_violated() -> None:
    # 状態を確かめずに CANCELLED にする → SHIPPED から CANCELLED は許されない
    f = _one(_code(_path("p1", TRUE, ["CANCELLED"])))
    assert (f.verdict, f.category) == ("VIOLATED", "state_mismatch")
    assert f.severity == "high"
    cex = f.counterexample
    assert cex is not None and cex.path_id == "p1"
    assert cex.assignments["before.order.status"] == "SHIPPED"
    assert "SHIPPED → CANCELLED" in f.message


def test_same_value_rewrite_is_not_transition() -> None:
    f = _one(_code(_path("p1", is_("PAID"), ["PAID"]), _rest("p2", ne(STATUS, S("PAID")))))
    assert f.verdict == "VERIFIED_IN_SCOPE"


def test_sequential_writes_chain_from_previous_value() -> None:
    ok = _path("p1", is_("PENDING"), ["PAID", "SHIPPED"])
    assert _one(_code(ok, _rest("p2", ne(STATUS, S("PENDING"))))).verdict == "VERIFIED_IN_SCOPE"
    bad = _path("p1", is_("PENDING"), ["CANCELLED", "SHIPPED"])
    f = _one(_code(bad, _rest("p2", ne(STATUS, S("PENDING")))))
    assert f.verdict == "VIOLATED"
    assert "CANCELLED → SHIPPED" in f.message


def test_transition_guard_is_checked() -> None:
    sm = _sm(guarded=True)
    paid = eq(ref("before.order.paid"), TRUE)
    unguarded = _path("p1", is_("PENDING"), ["PAID"])
    f = _one(_code(unguarded, _rest("p2", ne(STATUS, S("PENDING")))), sm)
    assert f.verdict == "VIOLATED"
    assert f.counterexample is not None
    assert f.counterexample.assignments["before.order.paid"] is False
    guarded = _path("p1", and_(is_("PENDING"), paid), ["PAID"])
    rest = _rest("p2", not_(and_(is_("PENDING"), paid)))
    assert _one(_code(guarded, rest), sm).verdict == "VERIFIED_IN_SCOPE"


def test_inexact_path_is_unknown() -> None:
    cause = UnknownCause(code="unsupported_syntax", detail="while は対応外")
    p = _path("p1", is_("PENDING"), ["PAID"], precision="incomplete", causes=[cause])
    assert _one(_code(p, _rest("p2", ne(STATUS, S("PENDING"))))).verdict == "UNKNOWN"


def test_may_write_is_unknown() -> None:
    p = _path("p1", TRUE, ["CANCELLED"], certainty="may")
    assert _one(_code(p)).verdict == "UNKNOWN"


def test_unbound_attribute_is_unknown() -> None:
    f = _one(_code(_path("p1", TRUE, ["PAID"])), domain=DomainSpec())
    assert f.verdict == "UNKNOWN"
    assert "見つからない" in f.message


def test_global_write_cause_is_unknown() -> None:
    cause = UnknownCause(code="limit_paths", detail="上限", may_affect=["write:order.status"])
    code = _code(_path("p1", is_("PENDING"), ["PAID"]), _rest("p2", ne(STATUS, S("PENDING"))))
    assert _one(code.model_copy(update={"unknown_causes": [cause]})).verdict == "UNKNOWN"


def test_uncovered_input_is_unknown() -> None:
    assert _one(_code(_path("p1", is_("PENDING"), ["PAID"]))).verdict == "UNKNOWN"


# ---------------------------------------------------------------- レビュー fix round 1
def test_write_to_initial_without_transition_is_unknown() -> None:
    # プローブ B: 前の状態を絞らずに初期状態 PENDING を代入 → 生成か遷移か判別できない
    f = _one(_code(_path("p1", TRUE, ["PENDING"])))
    assert (f.verdict, f.category) == ("UNKNOWN", "unsupported_analysis")
    assert "初期状態" in f.message


def test_write_to_initial_with_allowed_transition_is_judged_normally() -> None:
    sm = _sm().model_copy(
        update={
            "transitions": [
                *_sm().transitions,
                StateTransition(id="t5", source="CANCELLED", target="PENDING", event="reopen"),
            ]
        }
    )
    ok = _path("p1", is_("CANCELLED"), ["PENDING"])
    rest = _rest("p2", ne(STATUS, S("CANCELLED")))
    assert _one(_code(ok, rest), sm).verdict == "VERIFIED_IN_SCOPE"


def test_cause_touching_transition_guard_variable_is_unknown() -> None:
    cause = UnknownCause(
        code="unresolved_attribute", detail="paid", may_affect=["write:order.paid"]
    )
    p = _path("p1", and_(is_("PENDING"), eq(ref("before.order.paid"), TRUE)), ["PAID"])
    p = p.model_copy(update={"unknown_causes": [cause]})
    rest = _rest("p2", not_(and_(is_("PENDING"), eq(ref("before.order.paid"), TRUE))))
    assert _one(_code(p, rest), _sm(guarded=True)).verdict == "UNKNOWN"


# ---------------------------------------------------------------- レビュー fix round 2
def test_illegal_write_after_undecided_initial_write_is_violated() -> None:
    # プローブ B3: PENDING（生成か遷移か判別できない）の後の PENDING → SHIPPED は確定した違反
    f = _one(_code(_path("p1", TRUE, ["PENDING", "SHIPPED"])))
    assert (f.verdict, f.category) == ("VIOLATED", "state_mismatch")
    assert "PENDING → SHIPPED" in f.message


def test_guard_variable_without_binding_is_unknown() -> None:
    code = _code(_path("p1", is_("PENDING"), ["PAID"]), _rest("p2", ne(STATUS, S("PENDING"))))
    fs = check_state_machine(
        _sm(guarded=True), code, CONFIG, domain=DOMAIN, bound_variables=frozenset({"order.status"})
    )
    assert fs[0].verdict == "UNKNOWN"
    assert "order.paid" in fs[0].message
