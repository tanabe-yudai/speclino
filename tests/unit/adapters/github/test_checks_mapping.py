"""checks_mapping の単体試験（S19）。全組合せを確認する。"""

from __future__ import annotations

import pytest

from speclino.adapters.github.checks_mapping import to_check_state
from speclino.domain.run import ExecState, GateStatus


def test_queued_has_no_conclusion() -> None:
    assert to_check_state(None, "queued") == ("queued", None)


def test_running_has_no_conclusion() -> None:
    assert to_check_state(None, "running") == ("in_progress", None)


@pytest.mark.parametrize(
    ("gate_status", "expected_conclusion"),
    [
        ("fail", "failure"),
        ("pass", "success"),
        ("pass_with_exceptions", "neutral"),
    ],
)
def test_completed_with_gate_status(gate_status: GateStatus, expected_conclusion: str) -> None:
    assert to_check_state(gate_status, "completed") == ("completed", expected_conclusion)


def test_completed_without_gate_status_is_action_required() -> None:
    assert to_check_state(None, "completed") == ("completed", "action_required")


@pytest.mark.parametrize(
    "exec_state",
    ["failed", "awaiting_answer", "superseded", "cancelled"],
)
def test_non_completed_terminal_states_are_action_required(exec_state: ExecState) -> None:
    assert to_check_state(None, exec_state) == ("completed", "action_required")
    # gate_status があっても、completed でない限り action_required のまま
    assert to_check_state("pass", exec_state) == ("completed", "action_required")
