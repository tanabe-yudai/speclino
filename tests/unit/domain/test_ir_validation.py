"""不正な IR が拒否されること（空配列、型不整合、未定義フィールド）。"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from speclino.domain import DomainSpec, Event, Path
from speclino.domain.expr import Expr

EXPR = TypeAdapter(Expr)


def test_nary_empty_args_rejected() -> None:
    with pytest.raises(ValidationError):
        EXPR.validate_python({"op": "and", "args": []})


def test_ref_requires_namespace() -> None:
    with pytest.raises(ValidationError):
        EXPR.validate_python({"op": "ref", "path": "order.status"})


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        EXPR.validate_python({"op": "ref", "path": "before.x", "extra": 1})


def test_const_value_is_strict() -> None:
    # "1" は整数に黙って変換しない
    c = EXPR.validate_python({"op": "const", "type": "integer", "value": "1"})
    assert c.value == "1"  # 型整合の検査は spec_validator が行う（ここでは文字列のまま保持）
    b = EXPR.validate_python({"op": "const", "type": "boolean", "value": True})
    assert b.value is True


def test_domain_rejects_undefined_type() -> None:
    with pytest.raises(ValidationError):
        DomainSpec(variables={"order.status": "OrderStatus"})
    ok = DomainSpec(enums={"OrderStatus": ["PENDING"]}, variables={"order.status": "OrderStatus"})
    assert ok.type_of("order.status") == "OrderStatus"


def test_event_kind_requires_fields() -> None:
    with pytest.raises(ValidationError):
        Event(sequence=1, kind="call", phase="invoked")
    with pytest.raises(ValidationError):
        Event(sequence=1, kind="write", phase="applied")


def test_path_events_must_be_ordered_and_incomplete_needs_causes() -> None:
    base = {
        "path_id": "p",
        "guard": {"op": "ref", "path": "before.a"},
        "outcome": {"kind": "return"},
    }
    with pytest.raises(ValidationError):
        Path.model_validate(
            {
                **base,
                "precision": "exact_in_model",
                "events": [
                    {"sequence": 2, "kind": "return", "phase": "returned"},
                    {"sequence": 1, "kind": "return", "phase": "returned"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        Path.model_validate({**base, "precision": "incomplete"})
