"""semantic_diff（S28 / PoC 12 章）: 新規・既存・解消・判定不能化と、要求・実装の差分。"""

from __future__ import annotations

from speclino.conformance.engine import evaluate
from speclino.conformance.semantic_diff import (
    DiffSide,
    annotate_findings,
    classify_finding,
    diff_code_irs,
    diff_rules,
    path_content_hash,
    semantic_diff,
)
from speclino.domain import Findings, SourceRef
from speclino.domain.expr import and_, const, eq, or_, ref
from tests.fixtures.cancel_order import (
    CORRECT,
    DOMAIN,
    IS_ADMIN,
    IS_OWNER,
    P_CONFLICT,
    P_FORBIDDEN,
    PENDING,
    RESTORE_ADMIN_ONLY,
    SPEC,
    SUCCESS_EVENTS,
    SUCCESS_UPDATES,
    code_ir,
    path,
)


def _side(code, spec=SPEC, commit="c"):  # type: ignore[no-untyped-def]
    return DiffSide(commit, [spec], [code], evaluate(spec, code, DOMAIN))


def _status(diff, rid: str) -> str:  # type: ignore[no-untyped-def]
    return next(d.status for d in diff.findings if d.requirement_id == rid)


def test_classification_table() -> None:
    assert classify_finding(None, "VIOLATED") == "new_violation"
    assert classify_finding("VERIFIED_IN_SCOPE", "VIOLATED") == "new_violation"
    assert classify_finding("VIOLATED", "VIOLATED") == "existing_violation"
    assert classify_finding("VIOLATED", "VERIFIED_IN_SCOPE") == "resolved_violation"
    assert classify_finding("VIOLATED", "UNKNOWN") == "became_undecidable"
    assert classify_finding("VERIFIED_IN_SCOPE", "REVIEW") == "became_undecidable"
    assert classify_finding("UNKNOWN", "VERIFIED_IN_SCOPE") == "became_decidable"
    assert classify_finding("REVIEW", "UNKNOWN") == "changed"
    assert classify_finding("UNKNOWN", "UNKNOWN") == "unchanged"
    assert classify_finding(None, "VERIFIED_IN_SCOPE") == "added"
    assert classify_finding("VERIFIED_IN_SCOPE", None) == "removed"


def test_new_and_resolved_violation_between_correct_and_admin_only() -> None:
    base, head = _side(CORRECT, commit="base"), _side(RESTORE_ADMIN_ONLY, commit="head")
    diff = semantic_diff(base, head)
    assert diff.base_available and diff.base_commit == "base" and diff.head_commit == "head"
    assert _status(diff, "R-CANCEL-04") == "new_violation"
    assert _status(diff, "R-CANCEL-01") == "unchanged"
    assert diff.counts.new_violation == 1 and diff.counts.resolved_violation == 0
    # 逆向き（違反を直した PR）は解消
    back = semantic_diff(head, base)
    assert _status(back, "R-CANCEL-04") == "resolved_violation"
    assert back.counts.resolved_violation == 1 and back.counts.new_violation == 0
    # 実装差分: 成功経路が 2 経路（管理者 / 所有者）に分かれた
    beh = diff.behaviors[0]
    assert beh.behavior_id == "cancel_order" and beh.status == "changed"
    assert [p.path_id for p in beh.removed_paths] == ["p3_success"]
    assert [p.path_id for p in beh.added_paths] == ["p3_admin", "p4_owner"]
    assert beh.unchanged_paths == 2
    owner = beh.added_paths[1]
    assert owner.guard == (
        "(not(before.actor.is_admin) and before.actor.id == before.order.owner_id"
        " and before.order.status == PENDING)"
    )
    assert owner.events == [
        "write:order.status@applied = CANCELLED",
        "call:audit_record_command@invoked(action=CANCEL_ORDER, actor_id=before.actor.id)",
    ]
    assert owner.outcome == "http 204" and owner.precision == "exact_in_model"


def test_annotate_sets_four_valued_status_only() -> None:
    base, head = _side(CORRECT), _side(RESTORE_ADMIN_ONLY)
    diff = semantic_diff(base, head)
    annotated = annotate_findings(head.findings, diff)
    by = {f.requirement_id: f.base_head_status for f in annotated.findings}
    assert by["R-CANCEL-04"] == "new" and by["R-CANCEL-01"] == "unchanged"


def test_rename_and_reordering_do_not_produce_implementation_diff() -> None:
    """path_id / 行番号 / 経路の並びが違っても内容が同じなら差分なし（Golden 13 の要件）。"""
    renamed = code_ir(
        [
            path(
                "x3", and_(or_(IS_ADMIN, IS_OWNER), PENDING), 204, SUCCESS_EVENTS, SUCCESS_UPDATES
            ).model_copy(
                update={"source_refs": [SourceRef(snapshot_id="s", path="a.py", line_start=9)]}
            ),
            P_CONFLICT.model_copy(update={"path_id": "x2"}),
            P_FORBIDDEN.model_copy(update={"path_id": "x1"}),
        ]
    )
    [beh] = diff_code_irs([CORRECT], [renamed])
    assert beh.status == "unchanged" and beh.unchanged_paths == 3
    assert path_content_hash(P_FORBIDDEN) == path_content_hash(renamed.paths[2])


def test_rule_diff_by_content_hash_ignores_approval_ref() -> None:
    rules = SPEC.rules
    unchanged = [r.model_copy(update={"approval_ref": None}) for r in rules]
    assert {d.status for d in diff_rules(rules, unchanged)} == {"unchanged"}
    changed = [
        r.model_copy(
            update={"applicability": eq(ref("before.actor.is_admin"), const("boolean", True))}
        )
        if r.id == "R-CANCEL-04"
        else r
        for r in rules[:-1]
    ]
    by = {d.rule_id: d.status for d in diff_rules(rules, changed)}
    assert by["R-CANCEL-04"] == "changed" and by[rules[-1].id] == "removed"
    assert diff_rules([], rules)[0].status == "added"


def test_without_base_everything_is_added_and_base_error_kept() -> None:
    head = _side(CORRECT, commit="head")
    diff = semantic_diff(None, head, base_error="base に Spec IR が無い")
    assert not diff.base_available and diff.base_error == "base に Spec IR が無い"
    assert {d.status for d in diff.findings} == {"added"}
    assert diff.behaviors[0].status == "added" and diff.rules[0].status == "added"
    assert annotate_findings(head.findings, diff).findings[0].base_head_status is None


def test_multiple_findings_for_one_requirement_use_worst_verdict() -> None:
    head = _side(RESTORE_ADMIN_ONLY)
    doubled = Findings(
        findings=[
            *head.findings.findings,
            next(f for f in head.findings.findings if f.requirement_id == "R-CANCEL-04").model_copy(
                update={"id": "F-dup", "verdict": "VERIFIED_IN_SCOPE"}
            ),
        ]
    )
    diff = semantic_diff(None, DiffSide("h", [SPEC], [RESTORE_ADMIN_ONLY], doubled))
    assert _status(diff, "R-CANCEL-04") == "new_violation"


def test_output_is_deterministic() -> None:
    base, head = _side(CORRECT), _side(RESTORE_ADMIN_ONLY)
    a = semantic_diff(base, head).model_dump(mode="json")
    b = semantic_diff(base, head).model_dump(mode="json")
    assert a == b
