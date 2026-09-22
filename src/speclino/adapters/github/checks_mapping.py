"""ExecState/GateStatus から GitHub Checks の status/conclusion への写像（S19）。

保留（gate 未計算や失敗系の exec_state）は `neutral` にしない。GitHub 側で必須チェックを
満たす conclusion は success / skipped / neutral のみなので、保留は action_required にする。
"""

from __future__ import annotations

from speclino.domain.run import ExecState, GateStatus

_GATE_CONCLUSION: dict[GateStatus, str] = {
    "fail": "failure",
    "pass": "success",
    "pass_with_exceptions": "neutral",
}


def to_check_state(gate_status: GateStatus | None, exec_state: ExecState) -> tuple[str, str | None]:
    """(status, conclusion) を返す。conclusion は status が completed のときのみ非 None。"""
    if exec_state == "queued":
        return ("queued", None)
    if exec_state == "running":
        return ("in_progress", None)
    if exec_state == "completed":
        if gate_status is None:
            return ("completed", "action_required")
        return ("completed", _GATE_CONCLUSION[gate_status])
    # failed / awaiting_answer / superseded / cancelled
    return ("completed", "action_required")


__all__ = ["to_check_state"]
