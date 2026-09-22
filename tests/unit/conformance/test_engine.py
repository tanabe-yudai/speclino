"""conformance_engine の単体試験（手書き IR、PoC 10 章 / 17.1）。"""

from __future__ import annotations

import pytest

from speclino.conformance.engine import ConformanceEngine, EngineConfig, EngineInputError, evaluate
from speclino.domain import CodeIR, Findings, SpecIR, UnknownCause
from speclino.domain.expr import and_, const, eq, ref
from tests.fixtures import cancel_order as co


def run(code: CodeIR, spec: SpecIR = co.SPEC) -> Findings:
    return evaluate(spec, code, co.DOMAIN)


def by_id(findings: Findings) -> dict[str, object]:
    return {f.requirement_id: f for f in findings.findings}


def test_correct_implementation_is_verified() -> None:
    fs = run(co.CORRECT)
    assert fs.counts.VERIFIED_IN_SCOPE == 9, [
        (f.requirement_id, f.verdict, f.message) for f in fs.findings
    ]
    assert fs.coverage.requirements_total == 9
    assert fs.coverage.requirements_decided == 9


def test_restore_admin_only_yields_missing_effect_with_owner_counterexample() -> None:
    fs = run(co.RESTORE_ADMIN_ONLY)
    f = by_id(fs)["R-CANCEL-04"]
    assert f.verdict == "VIOLATED"
    assert f.category == "missing_effect"
    assert f.counterexample is not None
    assert f.counterexample.path_id == "p4_owner"
    assert "inventory_restore_command を 0 回" in (f.actual or "")
    # 他の要求は成立
    assert {k: v.verdict for k, v in by_id(fs).items() if k != "R-CANCEL-04"} == {
        k: "VERIFIED_IN_SCOPE" for k in by_id(fs) if k != "R-CANCEL-04"
    }


def test_409_to_400_is_outcome_mismatch() -> None:
    f = by_id(run(co.CONFLICT_400))["R-CANCEL-02"]
    assert (f.verdict, f.category) == ("VIOLATED", "outcome_mismatch")
    assert f.counterexample is not None
    assert f.counterexample.assignments["before.order.status"] != "PENDING"


def test_admin_only_auth_is_constraint_mismatch_for_owner() -> None:
    fs = by_id(run(co.ADMIN_ONLY_AUTH))
    # 所有者は成功すべきなのに 403 → 成功/拒否の区分が違う
    assert (fs["R-CANCEL-06"].verdict, fs["R-CANCEL-06"].category) == (
        "VIOLATED",
        "constraint_mismatch",
    )
    cex = fs["R-CANCEL-06"].counterexample
    assert cex is not None and cex.assignments["before.actor.is_admin"] is False
    # 無権限者の拒否自体は正しい
    assert fs["R-CANCEL-01"].verdict == "VERIFIED_IN_SCOPE"
    assert fs["R-CANCEL-07a"].verdict == "VERIFIED_IN_SCOPE"


def test_audit_with_owner_id_is_argument_mismatch_only_on_admin_path() -> None:
    f = by_id(run(co.AUDIT_OWNER_ID))["R-CANCEL-05"]
    assert (f.verdict, f.category) == ("VIOLATED", "argument_mismatch")
    # 所有者の経路では actor.id == owner_id なので一致する。反例は管理者かつ非所有者
    assert f.counterexample is not None
    assert f.counterexample.assignments["before.actor.is_admin"] is True
    assert (
        f.counterexample.assignments["before.actor.id"]
        != f.counterexample.assignments["before.order.owner_id"]
    )


def test_wrong_state_is_state_mismatch() -> None:
    f = by_id(run(co.WRONG_STATE))["R-CANCEL-03"]
    assert (f.verdict, f.category) == ("VIOLATED", "state_mismatch")
    assert "SHIPPED" in (f.actual or "")


def test_restore_twice_is_cardinality_mismatch() -> None:
    f = by_id(run(co.RESTORE_TWICE))["R-CANCEL-04"]
    assert (f.verdict, f.category) == ("VIOLATED", "cardinality_mismatch")


def test_incomplete_path_cannot_prove_absence() -> None:
    incomplete = co.code_ir(
        [
            co.P_FORBIDDEN,
            co.P_CONFLICT,
            co.Path(
                path_id="p3_success",
                guard=co.SUCCESS,
                state_updates=co.SUCCESS_UPDATES,
                events=[co.write_status(1), co.call_audit(2)],
                outcome=co.Outcome(kind="http_response", status=204),
                precision="incomplete",
                unknown_causes=[
                    UnknownCause(code="unresolved_call", detail="helper() を解決できない")
                ],
                source_refs=[co.CODE_SRC],
            ),
        ]
    )
    fs = by_id(run(incomplete))
    assert fs["R-CANCEL-04"].verdict == "UNKNOWN"
    assert fs["R-CANCEL-03"].verdict == "UNKNOWN"


def test_overapproximate_violation_is_review() -> None:
    over = co.code_ir(
        [
            co.P_FORBIDDEN,
            co.P_CONFLICT,
            co.path(
                "p3",
                co.SUCCESS,
                204,
                [co.write_status(1), co.call_audit(2)],
                co.SUCCESS_UPDATES,
                precision="overapproximate",
            ),
        ]
    )
    f = by_id(run(over))["R-CANCEL-04"]
    assert (f.verdict, f.category) == ("REVIEW", "potential_violation")


def test_uncovered_inputs_are_unknown() -> None:
    # 成功経路が無い（経路上限で打ち切られた想定）
    fs = by_id(run(co.code_ir([co.P_FORBIDDEN, co.P_CONFLICT])))
    assert fs["R-CANCEL-04"].verdict == "UNKNOWN"
    assert fs["R-CANCEL-01"].verdict == "VERIFIED_IN_SCOPE"


def test_vacuous_applicability_is_review() -> None:
    rule = co.RULES[3].model_copy(
        update={
            "applicability": and_(
                eq(ref("before.order.status"), const("OrderStatus", "PENDING")),
                eq(ref("before.order.status"), const("OrderStatus", "SHIPPED")),
            )
        }
    )
    spec = co.SPEC.model_copy(update={"rules": [rule]})
    f = run(co.CORRECT, spec).findings[0]
    assert (f.verdict, f.category) == ("REVIEW", "vacuous_applicability")


def test_unapproved_rule_is_stale_approval() -> None:
    rule = co.RULES[0].model_copy(update={"approval_ref": None})
    spec = co.SPEC.model_copy(update={"rules": [rule]})
    f = run(co.CORRECT, spec).findings[0]
    assert (f.verdict, f.category) == ("REVIEW", "stale_approval")


def test_type_error_is_input_error() -> None:
    rule = co.RULES[0].model_copy(
        update={"applicability": eq(ref("before.actor.is_admin"), const("integer", 1))}
    )
    spec = co.SPEC.model_copy(update={"rules": [rule]})
    with pytest.raises(EngineInputError):
        run(co.CORRECT, spec)


def test_deterministic_output() -> None:
    a = run(co.RESTORE_ADMIN_ONLY).model_dump_json()
    b = evaluate(
        co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN, EngineConfig(solver_timeout_ms=500)
    ).model_dump_json()
    assert a == b


def test_engine_can_be_reused_across_evaluations() -> None:
    engine = ConformanceEngine(co.DOMAIN)
    assert engine.evaluate(co.SPEC, co.CORRECT).counts.VIOLATED == 0
    assert engine.evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY).counts.VIOLATED == 1


def test_undocumented_operation_call_is_review_open_world() -> None:
    """仕様に無い操作の呼出しは REVIEW: undocumented_behavior（禁止と明記されていれば VIOLATED）。"""
    notify = co.Event(sequence=4, kind="call", phase="invoked", operation="notify_command")
    code = co.code_ir(
        [
            co.P_FORBIDDEN,
            co.P_CONFLICT,
            co.path("p3", co.SUCCESS, 204, [*co.SUCCESS_EVENTS, notify], co.SUCCESS_UPDATES),
        ]
    )
    fs = run(code)
    extra = next(f for f in fs.findings if f.category == "undocumented_behavior")
    assert extra.verdict == "REVIEW" and "notify_command" in extra.message and "p3" in extra.message
    assert fs.counts.REVIEW == 1 and fs.counts.VERIFIED_IN_SCOPE == 9
    # 禁止を明示した要求があれば VIOLATED（forbidden_effect）で、undocumented は出ない
    forbid = co._rule(
        "R-CANCEL-08",
        "R-CANCEL-08",
        co.SUCCESS,
        co._calls("notify_command", 0),
        "command_invocation",
    )
    spec = co.SPEC.model_copy(update={"rules": [*co.RULES, forbid]})
    fs2 = run(code, spec)
    assert not any(f.category == "undocumented_behavior" for f in fs2.findings)
    assert by_id(fs2)["R-CANCEL-08"].category == "forbidden_effect"
