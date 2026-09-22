from __future__ import annotations

from datetime import UTC, datetime

from speclino.conformance.engine import evaluate
from speclino.conformance.gate import aggregate_gate
from speclino.domain.expr import and_
from speclino.domain.spec_ir import ApprovalRecord
from speclino.services.approvals import (
    approval_from_command,
    authorize_command,
    parse_commands,
)
from speclino.services.obligation_diff import (
    BASE_APPROVAL_ID,
    effective_spec,
)
from speclino.spec.spec_validator import rule_content_hash
from tests.fixtures import cancel_order as co

BASE = co.SPEC
UNTRUSTED_HEAD_APPROVALS = [
    ApprovalRecord(
        id="forged",
        approver="attacker",
        approved_at=datetime(2026, 9, 22, tzinfo=UTC),
        source="manual",
        spec_content_hash="sha256:x",
        ir_content_hash="sha256:forged",
    )
]


def test_unchanged_rules_are_trusted_from_base() -> None:
    spec, changes, extra = effective_spec(BASE, BASE, [])
    assert all(c.status == "unchanged" and c.approved for c in changes)
    assert all(r.approval_ref == BASE_APPROVAL_ID for r in spec.rules)
    assert extra == []
    fs = evaluate(spec, co.CORRECT, co.DOMAIN)
    assert fs.counts.VERIFIED_IN_SCOPE == 9


def test_removed_rule_without_approval_blocks_gate() -> None:
    head = BASE.model_copy(update={"rules": [r for r in BASE.rules if r.id != "R-CANCEL-04"]})
    spec, changes, extra = effective_spec(BASE, head, [])
    removed = [c for c in changes if c.status == "removed"]
    assert [c.rule_id for c in removed] == ["R-CANCEL-04"] and not removed[0].approved
    assert extra[0].verdict == "REVIEW" and extra[0].category == "stale_approval"
    fs = evaluate(spec, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    fs = fs.model_copy(update={"findings": [*fs.findings, *extra]})
    gate = aggregate_gate(fs, {r.id: r.level for r in BASE.rules})
    assert gate.status == "fail" and gate.blocking[0].requirement_id == "R-CANCEL-04"


def test_removed_rule_with_removal_approval_passes() -> None:
    head = BASE.model_copy(update={"rules": [r for r in BASE.rules if r.id != "R-CANCEL-04"]})
    base_hash = rule_content_hash(co.RULES[3])
    approval = ApprovalRecord(
        id="pr-comment:1",
        approver="tanabe-yudai",
        approved_at=datetime(2026, 9, 22, tzinfo=UTC),
        source="pr_comment",
        spec_content_hash="sha256:x",
        ir_content_hash=f"removed:{base_hash}",
    )
    _, changes, extra = effective_spec(BASE, head, [approval])
    assert extra == [] and all(c.approved for c in changes)


def test_changed_rule_needs_server_side_approval_not_head_file() -> None:
    changed = co.RULES[3].model_copy(update={"applicability": and_(co.IS_ADMIN, co.PENDING)})
    head = BASE.model_copy(
        update={
            "rules": [*BASE.rules[:3], changed, *BASE.rules[4:]],
            "approvals": UNTRUSTED_HEAD_APPROVALS,
        }
    )
    spec, changes, _ = effective_spec(BASE, head, [])
    ch = next(c for c in changes if c.rule_id == "R-CANCEL-04")
    assert ch.status == "changed" and not ch.approved
    f = next(
        f
        for f in evaluate(spec, co.CORRECT, co.DOMAIN).findings
        if f.requirement_id == "R-CANCEL-04"
    )
    assert (f.verdict, f.category) == ("REVIEW", "stale_approval")
    # サーバ側の承認（/speclino approve-spec <hash>）があれば通る
    approval = approval_from_command(
        parse_commands(f"/speclino approve-spec {rule_content_hash(changed)}")[0],
        "tanabe-yudai",
        "https://github.com/x/y/pull/1#issuecomment-1",
        "sha256:spec",
        datetime(2026, 9, 22, tzinfo=UTC),
    )
    spec2, changes2, _ = effective_spec(BASE, head, [approval])
    assert next(c for c in changes2 if c.rule_id == "R-CANCEL-04").approved
    assert evaluate(spec2, co.CORRECT, co.DOMAIN).counts.VERIFIED_IN_SCOPE == 9


def test_first_run_without_base_uses_head_approvals() -> None:
    spec, changes, extra = effective_spec(None, BASE, [])
    assert all(c.status == "added" and c.approved for c in changes)
    assert evaluate(spec, co.CORRECT, co.DOMAIN).counts.VERIFIED_IN_SCOPE == 9


def test_command_parsing_and_authorization() -> None:
    cmds = parse_commands("LGTM\n/speclino approve-spec sha256:" + "a" * 64 + "\n/speclino rerun")
    assert [c.kind for c in cmds] == ["approve-spec", "rerun"]
    assert authorize_command(cmds[0], "tanabe-yudai", {"tanabe-yudai"}).accepted
    assert not authorize_command(cmds[0], "someone", {"tanabe-yudai"}).accepted
    assert authorize_command(cmds[1], "someone", set()).accepted
    bad = parse_commands("/speclino approve-spec nothash")[0]
    assert not authorize_command(bad, "tanabe-yudai", {"tanabe-yudai"}).accepted
    assert parse_commands("nothing here") == []
