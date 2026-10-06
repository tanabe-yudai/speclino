import pytest
from pydantic import ValidationError

from speclino.domain.uml import (
    Automaton,
    AutomatonTransition,
    CallLabel,
    CompiledSpec,
    ParseResult,
    SpecDiagnostic,
    StateMachine,
    StateTransition,
    TraceObligation,
)


def _auto() -> Automaton:
    return Automaton(
        states=["s0", "s1"],
        initial="s0",
        accepting=["s1"],
        transitions=[
            AutomatonTransition(
                id="m1", source="s0", target="s1", label=CallLabel(operation="Inventory.reserve")
            )
        ],
    )


def test_automaton_rejects_unknown_states() -> None:
    with pytest.raises(ValidationError):
        Automaton(states=["s0"], initial="s9", accepting=[], transitions=[])


def test_closed_only_for_activity() -> None:
    with pytest.raises(ValidationError):
        TraceObligation(
            id="seq1#placeOrder",
            diagram_kind="sequence",
            entry_operation="Order.place",
            mode="contains_subsequence",
            closed=True,
            automaton=_auto(),
            level="mandatory",
            spans=[],
        )


def _compiled() -> CompiledSpec:
    return CompiledSpec(
        parser_version="1", source_hash="sha256:" + "0" * 64, ir_hash="sha256:" + "1" * 64
    )


def _diag(severity: str) -> SpecDiagnostic:
    return SpecDiagnostic.model_validate(
        {"code": "E_X" if severity == "error" else "W_X", "severity": severity, "message": "m"}
    )


def test_parse_result_all_or_nothing() -> None:
    with pytest.raises(ValidationError):
        ParseResult(compiled=_compiled(), diagnostics=[_diag("error")])
    with pytest.raises(ValidationError):
        ParseResult(compiled=None, diagnostics=[])
    assert ParseResult(compiled=_compiled(), diagnostics=[_diag("warning")]).compiled is not None
    assert ParseResult(compiled=None, diagnostics=[_diag("error")]).compiled is None


def test_trace_mode_must_match_diagram_kind() -> None:
    with pytest.raises(ValidationError):
        TraceObligation(
            id="s#o",
            diagram_kind="sequence",
            entry_operation="A.b",
            mode="included_in",
            automaton=_auto(),
            level="mandatory",
        )
    with pytest.raises(ValidationError):
        TraceObligation(
            id="a#o",
            diagram_kind="activity",
            entry_operation="A.b",
            mode="contains_subsequence",
            automaton=_auto(),
            level="mandatory",
        )
    ok = TraceObligation(
        id="a#o",
        diagram_kind="activity",
        entry_operation="A.b",
        mode="included_in",
        closed=True,
        automaton=_auto(),
        level="mandatory",
    )
    assert ok.closed


def test_state_machine_rejects_unknown_states() -> None:
    t = StateTransition(id="t1", source="a", target="b")
    with pytest.raises(ValidationError):
        StateMachine(
            id="m", attribute="o.s", states=["a"], initial="a", transitions=[t], level="mandatory"
        )
    with pytest.raises(ValidationError):
        StateMachine(
            id="m",
            attribute="o.s",
            states=["a", "b"],
            initial="z",
            transitions=[t],
            level="mandatory",
        )
    assert StateMachine(
        id="m", attribute="o.s", states=["a", "b"], initial="a", transitions=[t], level="mandatory"
    )
