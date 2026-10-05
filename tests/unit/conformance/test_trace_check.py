"""順序（シーケンス図）と包含（アクティビティ図）の義務の検査（設計 5.1、手書き IR）。"""

from __future__ import annotations

from speclino.conformance.engine import EngineConfig
from speclino.conformance.trace_check import check_trace
from speclino.domain import (
    AnalysisLimits,
    CodeIR,
    DomainSpec,
    Event,
    Outcome,
    Path,
    SnapshotRef,
    UnknownCause,
)
from speclino.domain.common import Precision
from speclino.domain.expr import Binary, Expr, const, eq, ref
from speclino.domain.finding import Finding
from speclino.domain.uml import (
    Automaton,
    AutomatonTransition,
    CallLabel,
    EpsilonLabel,
    TraceObligation,
)

DOMAIN = DomainSpec(variables={"order.express": "boolean", "order.qty": "integer"})
EXPRESS = eq(ref("before.order.express"), const("boolean", True))
NORMAL = eq(ref("before.order.express"), const("boolean", False))
TRUE = const("boolean", True)
CONFIG = EngineConfig(solver_timeout_ms=1000)


def _t(
    id_: str, s: str, t: str, op: str | None = None, guard: Expr | None = None
) -> AutomatonTransition:
    label = CallLabel(operation=op) if op else EpsilonLabel()
    return AutomatonTransition(id=id_, source=s, target=t, label=label, guard=guard)


def _seq(transitions: list[AutomatonTransition], accepting: list[str]) -> TraceObligation:
    states = sorted({t.source for t in transitions} | {t.target for t in transitions})
    return TraceObligation(
        id="seq-place#OrderService.place",
        diagram_kind="sequence",
        entry_operation="OrderService.place",
        mode="contains_subsequence",
        automaton=Automaton(
            states=states, initial="s0", accepting=accepting, transitions=transitions
        ),
        level="mandatory",
    )


# Client->>OrderService: [m-1] place ; reserve ;
# alt [express] Shipping.express else Shipping.normal ;
# Mailer.notify
SEQ = _seq(
    [
        _t("m-1", "s0", "s0a", "OrderService.place"),
        _t("m0", "s0a", "s1", "Inventory.reserve"),
        _t("g1", "s1", "s2", guard=EXPRESS),
        _t("m1", "s2", "s4", "Shipping.express"),
        _t("g2", "s1", "s3", guard=NORMAL),
        _t("m2", "s3", "s4", "Shipping.normal"),
        _t("m3", "s4", "s5", "Mailer.notify"),
    ],
    ["s5"],
)


def _path(
    pid: str,
    guard: Expr,
    ops: list[str],
    precision: Precision = "exact_in_model",
    causes: list[UnknownCause] | None = None,
    may: frozenset[str] = frozenset(),
) -> Path:
    return Path(
        path_id=pid,
        guard=guard,
        events=[
            Event(
                sequence=i + 1,
                kind="call",
                phase="invoked",
                operation=op,
                certainty="may" if op in may else "must",
            )
            for i, op in enumerate(ops)
        ],
        outcome=Outcome(kind="return"),
        precision=precision,
        unknown_causes=causes or [],
    )


def _code(*paths: Path, causes: list[UnknownCause] | None = None) -> CodeIR:
    return CodeIR(
        behavior_id="place_order",
        entrypoint="app.orders.OrderService.place",
        snapshot=SnapshotRef(snapshot_id="test"),
        analyzer_version="test",
        limits=AnalysisLimits(depth=2, max_paths=64, solver_timeout_ms=1000),
        paths=list(paths),
        unknown_causes=causes or [],
    )


def _one(ob: TraceObligation, code: CodeIR, **kw: object) -> Finding:
    fs = check_trace(ob, code, CONFIG, domain=DOMAIN, **kw)  # type: ignore[arg-type]
    assert len(fs) == 1
    assert fs[0].requirement_id == ob.id
    return fs[0]


GOOD_EXPRESS = _path("p1", EXPRESS, ["Inventory.reserve", "Shipping.express", "Mailer.notify"])
GOOD_NORMAL = _path("p2", NORMAL, ["Inventory.reserve", "Shipping.normal", "Mailer.notify"])


# ---------------------------------------------------------------- sequence
def test_subsequence_satisfied_is_verified() -> None:
    f = _one(SEQ, _code(GOOD_EXPRESS, GOOD_NORMAL))
    assert (f.verdict, f.category) == ("VERIFIED_IN_SCOPE", "conformant"), f.message
    assert f.evidence_refs == ["p1", "p2"]
    assert f.severity == "info"


def test_missing_call_is_violated_with_counterexample() -> None:
    missing = _path("p2", NORMAL, ["Inventory.reserve", "Mailer.notify"])
    f = _one(SEQ, _code(GOOD_EXPRESS, missing))
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")
    assert f.severity == "high"
    cex = f.counterexample
    assert cex is not None and cex.path_id == "p2"
    assert cex.assignments["before.order.express"] is False
    assert "Inventory.reserve の後に Shipping.normal" in f.message


def test_extra_call_is_not_violation() -> None:
    extra = _path(
        "p1",
        EXPRESS,
        ["Audit.log", "Inventory.reserve", "Cache.put", "Shipping.express"]
        + ["Mailer.notify", "Audit.log"],
    )
    f = _one(SEQ, _code(extra, GOOD_NORMAL))
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message


def test_out_of_order_is_violated() -> None:
    swapped = _path("p1", EXPRESS, ["Inventory.reserve", "Mailer.notify", "Shipping.express"])
    f = _one(SEQ, _code(swapped, GOOD_NORMAL))
    assert f.verdict == "VIOLATED"
    assert f.counterexample is not None and f.counterexample.path_id == "p1"


def test_path_not_splitting_on_alt_guard_must_satisfy_both_branches() -> None:
    # 経路が express で分岐しない → normal の入力でも express を呼ぶ = 違反
    single = _path("p0", TRUE, ["Inventory.reserve", "Shipping.express", "Mailer.notify"])
    f = _one(SEQ, _code(single))
    assert f.verdict == "VIOLATED"
    assert f.counterexample is not None
    assert f.counterexample.assignments["before.order.express"] is False


def test_guard_unsat_path_skipped() -> None:
    # opt [express] だけの図: normal の経路は図のガードと両立しないので対象外
    opt = _seq(
        [
            _t("m0", "s0", "s1", "Inventory.reserve"),
            _t("g1", "s1", "s2", guard=EXPRESS),
            _t("m1", "s2", "s3", "Shipping.express"),
        ],
        ["s3"],
    )
    nothing = _path("p2", NORMAL, [])
    f = _one(opt, _code(GOOD_EXPRESS, nothing))
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert f.evidence_refs == ["p1"]


def test_inexact_path_is_unknown() -> None:
    loop_cause = UnknownCause(code="unsupported_syntax", detail="while は対応外")
    inexact = _path(
        "p2",
        NORMAL,
        ["Inventory.reserve", "Shipping.normal", "Mailer.notify"],
        precision="incomplete",
        causes=[loop_cause],
    )
    f = _one(SEQ, _code(GOOD_EXPRESS, inexact))
    assert (f.verdict, f.category) == ("UNKNOWN", "unsupported_analysis")
    assert "p2" in f.message


def test_exact_path_with_affecting_cause_is_unknown() -> None:
    cause = UnknownCause(
        code="unresolved_call", detail="動的呼出し", may_affect=["call:Shipping.normal"]
    )
    p2 = _path(
        "p2", NORMAL, ["Inventory.reserve", "Shipping.normal", "Mailer.notify"], causes=[cause]
    )
    assert _one(SEQ, _code(GOOD_EXPRESS, p2)).verdict == "UNKNOWN"


def test_cause_not_touching_diagram_calls_does_not_block() -> None:
    cause = UnknownCause(code="unresolved_call", detail="ログ", may_affect=["call:Audit.log"])
    p2 = _path(
        "p2", NORMAL, ["Inventory.reserve", "Shipping.normal", "Mailer.notify"], causes=[cause]
    )
    assert _one(SEQ, _code(GOOD_EXPRESS, p2)).verdict == "VERIFIED_IN_SCOPE"


def test_may_event_on_involved_operation_is_unknown() -> None:
    p2 = _path(
        "p2",
        NORMAL,
        ["Inventory.reserve", "Shipping.normal", "Mailer.notify"],
        may=frozenset({"Shipping.normal"}),
    )
    assert _one(SEQ, _code(GOOD_EXPRESS, p2)).verdict == "UNKNOWN"


def test_path_limit_exceeded_is_unknown() -> None:
    limit = UnknownCause(code="limit_paths", detail="経路数が上限 64 を超えた")
    assert _one(SEQ, _code(GOOD_EXPRESS, GOOD_NORMAL, causes=[limit])).verdict == "UNKNOWN"


def test_uncovered_input_is_unknown() -> None:
    # normal の入力に対応する経路が無い（列挙が不完全）→ 成立と言わない
    f = _one(SEQ, _code(GOOD_EXPRESS))
    assert f.verdict == "UNKNOWN"


def test_no_paths_is_unknown() -> None:
    assert _one(SEQ, _code()).verdict == "UNKNOWN"


def test_unbound_element_is_unknown() -> None:
    def resolve(op: str) -> str | None:
        return None if op == "Mailer.notify" else op

    f = _one(SEQ, _code(GOOD_EXPRESS, GOOD_NORMAL), resolve_operation=resolve)
    assert (f.verdict, f.category) == ("UNKNOWN", "unsupported_analysis")
    assert "対応する操作が見つからない" in f.message and "Mailer.notify" in f.message


def test_resolve_operation_maps_to_business_ids() -> None:
    ids = {"Inventory.reserve": "inventory_reserve_command"}
    p1 = _path("p1", TRUE, ["inventory_reserve_command"])
    ob = _seq([_t("m0", "s0", "s1", "Inventory.reserve")], ["s1"])
    f = _one(ob, _code(p1), resolve_operation=lambda op: ids.get(op))
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message


def test_loop_is_zero_or_more() -> None:
    # loop: s1 -[m1 Shipping.express]-> s1（0 回以上）、その後 notify
    ob = _seq(
        [
            _t("m0", "s0", "s1", "Inventory.reserve"),
            _t("m1", "s1", "s1", "Shipping.express"),
            _t("m3", "s1", "s2", "Mailer.notify"),
        ],
        ["s2"],
    )
    zero = _path("p1", EXPRESS, ["Inventory.reserve", "Mailer.notify"])
    many = _path("p2", NORMAL, ["Inventory.reserve"] + ["Shipping.express"] * 5 + ["Mailer.notify"])
    assert _one(ob, _code(zero, many)).verdict == "VERIFIED_IN_SCOPE"


def test_guard_without_domain_is_unknown() -> None:
    fs = check_trace(SEQ, _code(GOOD_EXPRESS, GOOD_NORMAL), CONFIG)
    assert fs[0].verdict == "UNKNOWN"
    assert "型" in fs[0].message


def test_deterministic_finding_id() -> None:
    code = _code(GOOD_EXPRESS, _path("p2", NORMAL, ["Inventory.reserve"]))
    assert _one(SEQ, code).id == _one(SEQ, code).id


# ---------------------------------------------------------------- activity
POSITIVE = Binary(op="gt", left=ref("before.order.qty"), right=const("integer", 0))
NON_POSITIVE = Binary(op="le", left=ref("before.order.qty"), right=const("integer", 0))


def _activity(closed: bool) -> TraceObligation:
    return TraceObligation(
        id="act-place#OrderService.place",
        diagram_kind="activity",
        entry_operation="OrderService.place",
        mode="included_in",
        closed=closed,
        automaton=Automaton(
            states=["start", "checked", "d", "reserved", "end"],
            initial="start",
            accepting=["end"],
            transitions=[
                _t("n1", "start", "checked", "Validator.check"),
                _t("e1", "checked", "d"),
                _t("e2", "d", "reserved", "Inventory.reserve", POSITIVE),
                _t("e3", "d", "end", guard=NON_POSITIVE),
                _t("e4", "reserved", "end"),
            ],
        ),
        level="mandatory",
    )


ACT_OK = [
    _path("a1", POSITIVE, ["Validator.check", "Inventory.reserve"]),
    _path("a2", NON_POSITIVE, ["Validator.check"]),
]


def test_activity_conforming_is_verified() -> None:
    assert _one(_activity(True), _code(*ACT_OK)).verdict == "VERIFIED_IN_SCOPE"


def test_activity_closed_extra_call_violated() -> None:
    extra = _path("a1", POSITIVE, ["Validator.check", "Audit.log", "Inventory.reserve"])
    f = _one(_activity(True), _code(extra, ACT_OK[1]))
    assert (f.verdict, f.category) == ("VIOLATED", "forbidden_effect")
    assert f.counterexample is not None and f.counterexample.path_id == "a1"
    assert "Audit.log" in f.message
    assert isinstance(f.counterexample.assignments["before.order.qty"], int)


def test_activity_open_extra_call_review() -> None:
    extra = _path("a1", POSITIVE, ["Validator.check", "Audit.log", "Inventory.reserve"])
    f = _one(_activity(False), _code(extra, ACT_OK[1]))
    assert (f.verdict, f.category) == ("REVIEW", "undocumented_behavior")


def test_activity_closed_wrong_branch_violated() -> None:
    # qty <= 0 なのに reserve する
    wrong = _path("a2", NON_POSITIVE, ["Validator.check", "Inventory.reserve"])
    f = _one(_activity(True), _code(ACT_OK[0], wrong))
    assert f.verdict == "VIOLATED"
    assert f.counterexample is not None and f.counterexample.path_id == "a2"


def test_activity_may_call_is_unknown() -> None:
    maybe = _path(
        "a1",
        POSITIVE,
        ["Validator.check", "Audit.log", "Inventory.reserve"],
        may=frozenset({"Audit.log"}),
    )
    assert _one(_activity(True), _code(maybe, ACT_OK[1])).verdict == "UNKNOWN"


def test_epsilon_only_cycle_from_opt_in_loop() -> None:
    # loop { opt [express] Shipping.express } ; Mailer.notify（S63 が作る ε だけの閉路を含む）
    ob = _seq(
        [
            _t("m0", "s0", "head", "Inventory.reserve"),
            _t("o1.enter", "head", "body", guard=EXPRESS),
            _t("m1", "body", "join", "Shipping.express"),
            _t("o1.skip", "head", "join"),
            _t("l1.back", "join", "head"),
            _t("l1.exit", "head", "tail"),
            _t("m3", "tail", "s9", "Mailer.notify"),
        ],
        ["s9"],
    )
    ok = _path("p1", TRUE, ["Inventory.reserve", "Shipping.express", "Mailer.notify"])
    assert _one(ob, _code(ok)).verdict == "VERIFIED_IN_SCOPE"
    missing = _path("p1", TRUE, ["Inventory.reserve"])
    f = _one(ob, _code(missing))
    assert f.verdict == "VIOLATED"
    assert "Mailer.notify" in f.message
