"""ガード → Expr（grammar.md 3 章）。"""

from __future__ import annotations

import pytest

from speclino.domain.expr import Binary, Const, Nary, Not, Ref, and_, const, eq, not_, ref
from speclino.spec.uml._parse import Cursor, LineError
from speclino.spec.uml.guard import parse_guard, render_expr
from speclino.spec.uml.lexer import tokenize_line
from speclino.spec.uml.types import Coverage


def _parse(text: str) -> object:
    toks = tokenize_line(text, 1)
    cov = Coverage()
    cov.add(toks)
    cur = Cursor(toks, cov)
    e = parse_guard(cur)
    cur.expect_end()
    assert cov.unclassified() == []
    return e


def _err(text: str) -> str:
    with pytest.raises(LineError) as ei:
        _parse(text)
    return ei.value.code


def test_comparison_with_enum_literal() -> None:
    assert _parse("before.order.status == OrderStatus.PENDING") == eq(
        ref("before.order.status"), const("OrderStatus", "PENDING")
    )


def test_precedence_and_flattening() -> None:
    e = _parse("before.a < 3 and before.b or not before.c and true")
    assert isinstance(e, Nary) and e.op == "or"
    left, right = e.args
    assert left == and_(
        Binary(op="lt", left=ref("before.a"), right=const("integer", 3)), ref("before.b")
    )
    assert right == and_(not_(ref("before.c")), const("boolean", True))


def test_parentheses_do_not_create_nodes_and_arith_is_left_assoc() -> None:
    e = _parse("(before.a - before.b - 1) >= -2")
    assert isinstance(e, Binary) and e.op == "ge"
    assert e.right == const("integer", -2)
    assert isinstance(e.left, Binary) and isinstance(e.left.left, Binary)


def test_rejects_natural_language() -> None:
    assert _err("在庫が足りない") == "E_GUARD_SYNTAX"
    assert _err("stock is low") == "E_GUARD_SYNTAX"
    assert _err("status == PENDING") == "E_GUARD_SYNTAX"


def test_rejects_chained_comparison_and_non_boolean() -> None:
    assert _err("before.a < before.b < before.c") == "E_GUARD_SYNTAX"
    assert _err("before.a + 1") == "E_GUARD_TYPE"
    assert _err("OrderStatus.PAID") == "E_GUARD_TYPE"
    assert _err("3 and before.x") == "E_GUARD_TYPE"


def test_rejects_unclosed_paren_and_empty() -> None:
    assert _err("(before.a") == "E_GUARD_SYNTAX"
    assert _err("") == "E_GUARD_SYNTAX"


@pytest.mark.parametrize(
    "text",
    [
        "before.a == 1",
        "not (before.a == 1)",
        "not before.flag",
        "(before.a - before.b) - -3 < after.c",
        "before.a - (before.b + 1) != 0",
        "(before.a and before.b) and before.c",
        "(before.x == E.A or before.y) and not (outcome.status >= 400)",
    ],
)
def test_render_roundtrip(text: str) -> None:
    e = _parse(text)
    assert _parse(render_expr(e)) == e  # type: ignore[arg-type]


def test_render_is_canonical() -> None:
    e = Nary(
        op="and",
        args=[
            Not(op="not", arg=Ref(op="ref", path="before.a")),
            Const(op="const", type="boolean", value=False),
        ],
    )
    assert render_expr(e) == "(not before.a) and false"
