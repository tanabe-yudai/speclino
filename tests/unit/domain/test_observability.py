"""S71: 可観測性の表示の型。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from speclino.domain.observability import Ratio, UnknownBreakdown, WorkspaceHealth


def test_ratio_rejects_zero_denominator() -> None:
    # 測っていないものを 0% や 100% にしない: 分母 0 の Ratio は作れない（呼び出し側が None にする）
    with pytest.raises(ValidationError):
        Ratio(numerator=0, denominator=0, rate=0.0)


def test_ratio_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        Ratio(numerator=2, denominator=1, rate=2.0)


def test_empty_health_is_unmeasured() -> None:
    h = WorkspaceHealth(
        workspace="w",
        generated_at=datetime(2026, 10, 5, tzinfo=UTC),
        current_generation=None,
        fetch_window_days=30,
    )
    assert h.llm is None and h.projects == [] and h.rules == []
    assert UnknownBreakdown().model_dump() == {
        "unbound": 0,
        "inexact_path": 0,
        "analysis_limit": 0,
        "missing_finding": 0,
        "lock": 0,
        "other": 0,
    }
