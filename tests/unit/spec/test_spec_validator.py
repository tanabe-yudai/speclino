from __future__ import annotations

from pathlib import Path

from speclino.domain.expr import and_, const, eq, ref
from speclino.domain.spec_ir import ExpressionAssertion
from speclino.spec import (
    load_bindings,
    load_domain,
    load_spec_ir,
    rules_content_hash,
    validate_spec_ir,
)
from tests.fixtures import cancel_order as co

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "orders"


def codes(report: object) -> set[str]:
    return {d.code for d in report.diagnostics}  # type: ignore[attr-defined]


def test_examples_orders_is_ok_with_pending_ir_hash() -> None:
    spec = load_spec_ir(EXAMPLE / "specs" / "ir" / "cancel_order.spec-ir.json")
    domain = load_domain(EXAMPLE / ".speclino" / "domain.yaml")
    bindings = load_bindings(EXAMPLE / ".speclino" / "bindings.yaml")
    md = (EXAMPLE / "specs" / "orders-cancel.md").read_text(encoding="utf-8")
    report = validate_spec_ir(spec, domain, bindings, md)
    assert report.ok, [d.model_dump() for d in report.errors]
    assert codes(report) <= {"W_HASH_PENDING"}


def test_fixture_spec_is_ok_without_markdown() -> None:
    report = validate_spec_ir(co.SPEC, co.DOMAIN)
    assert report.ok
    assert "W_IR_HASH_MISMATCH" in codes(report)  # sha256:demo は現在の IR と一致しない


def _with(rule_updates: dict[str, object], index: int = 3) -> object:
    rule = co.RULES[index].model_copy(update=rule_updates)
    return co.SPEC.model_copy(update={"rules": [rule]})


def test_undefined_ref_and_type_errors() -> None:
    spec = _with({"applicability": eq(ref("before.order.color"), const("OrderStatus", "PENDING"))})
    assert "E_REF_UNDEFINED" in codes(validate_spec_ir(spec, co.DOMAIN))  # type: ignore[arg-type]
    spec = _with({"applicability": eq(ref("before.actor.is_admin"), const("integer", 1))})
    assert "E_TYPE" in codes(validate_spec_ir(spec, co.DOMAIN))  # type: ignore[arg-type]
    spec = _with({"applicability": ref("before.actor.id")})
    assert "E_TYPE" in codes(validate_spec_ir(spec, co.DOMAIN))  # type: ignore[arg-type]


def test_applicability_must_reference_before() -> None:
    spec = _with({"applicability": eq(ref("after.order.status"), const("OrderStatus", "PENDING"))})
    assert "E_APPLICABILITY_NAMESPACE" in codes(validate_spec_ir(spec, co.DOMAIN))  # type: ignore[arg-type]


def test_unbound_operation_and_argument() -> None:
    bindings = load_bindings(EXAMPLE / ".speclino" / "bindings.yaml")
    a = co.RULES[3].assertion.model_copy(update={"operation_or_target": "shipping_command"})
    spec = _with({"assertion": a})
    assert "E_OPERATION_UNBOUND" in codes(validate_spec_ir(spec, co.DOMAIN, bindings))  # type: ignore[arg-type]
    a = co.RULES[3].assertion.model_copy(
        update={"arguments_equal": {"sku": ref("before.order.items")}}
    )
    spec = _with({"assertion": a})
    assert "E_ARG_UNBOUND" in codes(validate_spec_ir(spec, co.DOMAIN, bindings))  # type: ignore[arg-type]


def test_duplicate_rule_id() -> None:
    spec = co.SPEC.model_copy(update={"rules": [co.RULES[0], co.RULES[0]]})
    assert "E_DUP_RULE_ID" in codes(validate_spec_ir(spec, co.DOMAIN))


def test_contradiction_same_applicability_different_status() -> None:
    r1 = co.RULES[0]
    r2 = r1.model_copy(
        update={
            "id": "R-X",
            "assertion": ExpressionAssertion(kind="expression", expr=co.status_is(409)),
        }
    )
    spec = co.SPEC.model_copy(update={"rules": [r1, r2]})
    assert "E_CONTRADICTION" in codes(validate_spec_ir(spec, co.DOMAIN))


def test_spec_hash_mismatch_is_error() -> None:
    report = validate_spec_ir(co.SPEC, co.DOMAIN, None, "# changed spec text")
    assert "E_SPEC_HASH_MISMATCH" in codes(report)
    assert not report.ok


def test_unapproved_is_warning_only() -> None:
    spec = _with({"approval_ref": None})
    report = validate_spec_ir(spec, co.DOMAIN)  # type: ignore[arg-type]
    assert report.ok and "W_UNAPPROVED" in codes(report)


def test_rules_hash_is_order_independent_and_ignores_approval_ref() -> None:
    a = rules_content_hash(co.RULES)
    b = rules_content_hash(list(reversed(co.RULES)))
    c = rules_content_hash([r.model_copy(update={"approval_ref": "other"}) for r in co.RULES])
    assert a == b == c
    d = rules_content_hash(
        [
            co.RULES[0].model_copy(update={"applicability": and_(co.IS_ADMIN, co.PENDING)}),
            *co.RULES[1:],
        ]
    )
    assert d != a
