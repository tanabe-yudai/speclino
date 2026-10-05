import pytest
from pydantic import ValidationError

from speclino.domain.uml import Automaton, AutomatonTransition, CallLabel, TraceObligation


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
