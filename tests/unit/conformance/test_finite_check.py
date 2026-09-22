"""finite_check の単体試験（PoC 3.1: 有限ドメインでの検算）。"""

from __future__ import annotations

import pytest

from speclino.conformance.finite_check import (
    Bounds,
    FiniteCheckSkipped,
    Interpreter,
    check_rule,
    cross_check,
    enumerate_assignments,
    first_counterexample,
)
from speclino.domain import CodeIR, DomainSpec, Path
from speclino.domain.expr import Binary, and_, const, eq, ne, not_, or_, ref
from tests.fixtures import cancel_order as co


def _path_by_id(code: CodeIR, path_id: str) -> Path:
    return next(p for p in code.paths if p.path_id == path_id)


# ---------------------------------------------------------------- Interpreter
def test_interpreter_not_and_or() -> None:
    env = {"before.a": True, "before.b": False}
    interp = Interpreter(env)
    assert interp.eval(not_(ref("before.a"))) is False
    assert interp.eval(and_(ref("before.a"), ref("before.b"))) is False
    assert interp.eval(or_(ref("before.a"), ref("before.b"))) is True
    assert interp.eval(and_(ref("before.a"), not_(ref("before.b")))) is True


def test_interpreter_comparisons_and_arithmetic() -> None:
    env = {"before.x": 3, "before.y": 5}
    interp = Interpreter(env)
    assert interp.eval(Binary(op="lt", left=ref("before.x"), right=ref("before.y"))) is True
    assert interp.eval(Binary(op="le", left=ref("before.x"), right=const("integer", 3))) is True
    assert interp.eval(Binary(op="gt", left=ref("before.y"), right=ref("before.x"))) is True
    assert interp.eval(Binary(op="ge", left=ref("before.x"), right=ref("before.y"))) is False
    assert interp.eval(Binary(op="add", left=ref("before.x"), right=ref("before.y"))) == 8
    assert interp.eval(Binary(op="sub", left=ref("before.y"), right=ref("before.x"))) == 2
    assert interp.eval(eq(ref("before.x"), const("integer", 3))) is True
    assert interp.eval(ne(ref("before.x"), const("integer", 3))) is False


def test_interpreter_opaque_identity() -> None:
    # opaque は同一性だけ比較する。同じ記号なら等しく、違う記号なら異なる。
    env = {"before.u1": "a", "before.u2": "a", "before.u3": "b"}
    interp = Interpreter(env)
    assert interp.eval(eq(ref("before.u1"), ref("before.u2"))) is True
    assert interp.eval(eq(ref("before.u1"), ref("before.u3"))) is False
    assert interp.eval(ne(ref("before.u1"), ref("before.u3"))) is True


# ---------------------------------------------------------------- enumerate_assignments
def test_enumerate_assignments_raises_finite_check_skipped_over_limit() -> None:
    domain = DomainSpec(variables={"a": "boolean", "b": "boolean", "c": "boolean"})
    refs = [ref("before.a"), ref("before.b"), ref("before.c")]
    with pytest.raises(FiniteCheckSkipped):
        list(enumerate_assignments(domain, refs, Bounds(max_assignments=4)))  # 2**3 == 8 > 4


def test_enumerate_assignments_opaque_yields_equal_and_different() -> None:
    domain = DomainSpec(opaque_types=["UserId"], variables={"u1": "UserId", "u2": "UserId"})
    refs = [ref("before.u1"), ref("before.u2")]
    combos = list(enumerate_assignments(domain, refs))
    assert any(a["before.u1"] == a["before.u2"] for a in combos)
    assert any(a["before.u1"] != a["before.u2"] for a in combos)


# ---------------------------------------------------------------- check_rule
def test_check_rule_holds_and_violated() -> None:
    rule = co.RULES[3]  # R-CANCEL-04: 在庫復元を 1 回呼ぶ
    assert check_rule(rule, _path_by_id(co.CORRECT, "p3_success"), co.DOMAIN) == "holds"
    assert check_rule(rule, _path_by_id(co.RESTORE_ADMIN_ONLY, "p4_owner"), co.DOMAIN) == "violated"


def test_check_rule_not_applicable_when_guard_contradicts_applicability() -> None:
    # R-CANCEL-01（no not(AUTHORIZED) が適用条件）は成功経路の guard（SUCCESS）と両立しない
    rule = co.RULES[0]
    assert check_rule(rule, _path_by_id(co.CORRECT, "p3_success"), co.DOMAIN) == "not_applicable"


def test_check_rule_respects_domain_assumptions() -> None:
    """入口仮定（domain.assumptions）に反する割当は対象外（engine の検査式と同じ）。"""
    rule = co.RULES[3]  # R-CANCEL-04
    owner_path = _path_by_id(co.RESTORE_ADMIN_ONLY, "p4_owner")  # 非管理者の所有者経路
    assert check_rule(rule, owner_path, co.DOMAIN) == "violated"
    admins_only = co.DOMAIN.model_copy(update={"assumptions": [ref("before.actor.is_admin")]})
    # 「実行者は常に管理者」と仮定すると所有者経路は到達せず、反例も出ない
    assert check_rule(rule, owner_path, admins_only) == "not_applicable"
    assert first_counterexample(rule, owner_path, admins_only) is None


# ---------------------------------------------------------------- first_counterexample
def test_first_counterexample_restore_admin_only_r_cancel_04() -> None:
    rule = co.RULES[3]  # R-CANCEL-04
    path = _path_by_id(co.RESTORE_ADMIN_ONLY, "p4_owner")
    cex = first_counterexample(rule, path, co.DOMAIN)
    assert cex is not None
    assert cex["before.actor.is_admin"] is False


def test_first_counterexample_none_when_no_violation() -> None:
    rule = co.RULES[3]
    path = _path_by_id(co.CORRECT, "p3_success")
    assert first_counterexample(rule, path, co.DOMAIN) is None


# ---------------------------------------------------------------- cross_check
@pytest.mark.parametrize(
    "code",
    [
        co.CORRECT,
        co.RESTORE_ADMIN_ONLY,
        co.CONFLICT_400,
        co.ADMIN_ONLY_AUTH,
        co.AUDIT_OWNER_ID,
        co.WRONG_STATE,
        co.RESTORE_TWICE,
    ],
    ids=[
        "CORRECT",
        "RESTORE_ADMIN_ONLY",
        "CONFLICT_400",
        "ADMIN_ONLY_AUTH",
        "AUDIT_OWNER_ID",
        "WRONG_STATE",
        "RESTORE_TWICE",
    ],
)
def test_cross_check_agrees_with_engine(code: CodeIR) -> None:
    report = cross_check(co.SPEC, code, co.DOMAIN)
    assert report.disagreements == []
    assert report.agreements > 0


def test_cross_check_skips_vacuous_and_stale_approval_rules() -> None:
    rule = co.RULES[3].model_copy(
        update={
            "applicability": and_(
                eq(ref("before.order.status"), const("OrderStatus", "PENDING")),
                eq(ref("before.order.status"), const("OrderStatus", "SHIPPED")),
            )
        }
    )
    spec = co.SPEC.model_copy(update={"rules": [rule]})
    report = cross_check(spec, co.CORRECT, co.DOMAIN)
    assert report.disagreements == []
    assert report.agreements == 0
    assert len(report.skipped) == 1


def test_may_events_follow_engine_range_semantics() -> None:
    """may イベントは回数の範囲 [must, must+may] で見る（codex レビュー指摘の回帰試験）。

    - 0 回要求に must 1 件 + may 1 件: 範囲 [1, 2] は全て不成立 → violated（uncertain にしない）
    - 1 回要求に must 0 件 + may 1 件: 範囲 [0, 1] で成否が混在 → uncertain
    """
    rule = co.RULES[3]  # R-CANCEL-04: 在庫復元を 1 回
    forbid = rule.model_copy(
        update={
            "assertion": rule.assertion.model_copy(
                update={"count": rule.assertion.count.model_copy(update={"value": 0})}
            )
        }
    )
    may_restore = co.call_restore(4).model_copy(update={"certainty": "may"})
    two = co.path("p", co.SUCCESS, 204, [*co.SUCCESS_EVENTS, may_restore], co.SUCCESS_UPDATES)
    assert check_rule(forbid, two, co.DOMAIN) == "violated"
    may_only = co.call_restore(2).model_copy(update={"certainty": "may"})
    maybe = co.path(
        "p",
        co.SUCCESS,
        204,
        [co.write_status(1), may_only, co.call_audit(3)],
        co.SUCCESS_UPDATES,
    )
    assert check_rule(rule, maybe, co.DOMAIN) == "uncertain"
    # engine は同じ経路を REVIEW にするので cross_check では比較対象外（不一致にならない）
    report = cross_check(co.SPEC, co.code_ir([co.P_FORBIDDEN, co.P_CONFLICT, maybe]), co.DOMAIN)
    assert report.disagreements == []
