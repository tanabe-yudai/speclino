"""例外の免除（AC-009）: 期限内・対象一致の VIOLATED だけ免除し、適合には書き換えない。"""

from __future__ import annotations

from datetime import date

from speclino.conformance.engine import evaluate
from speclino.conformance.exceptions import waivers_for
from speclino.conformance.gate import aggregate_gate
from speclino.domain.policy import PolicyExceptions
from speclino.verifiers.exceptions import load_policy_exceptions
from tests.fixtures import cancel_order as co


def exceptions(**overrides: object) -> PolicyExceptions:
    entry = {
        "id": "EXC-1",
        "rule": "R-CANCEL-04",
        "target": {"paths": ["**"]},  # 対象は必須。仕様側の要求はコードのパスで絞る
        "reason": "在庫復元は別バッチで行う暫定運用",
        "approver": "tanabe-yudai",
        "expires_on": "2026-10-31",
        "alternative": "夜間バッチで復元し、監視で検出する",
    }
    entry.update(overrides)
    return load_policy_exceptions({"exceptions": [entry]})


def test_valid_exception_waives_violation_and_gate_passes_with_exceptions() -> None:
    fs = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    waivers = waivers_for(fs, exceptions(), date(2026, 9, 22))
    assert [w.requirement_id for w in waivers] == ["R-CANCEL-04"]
    gate = aggregate_gate(
        fs, {r.id: r.level for r in co.RULES}, waived_ids=frozenset(w.finding_id for w in waivers)
    )
    assert gate.status == "pass_with_exceptions" and len(gate.waived) == 1
    # verdict は VIOLATED のまま（適合へ書き換えない）
    assert next(f for f in fs.findings if f.requirement_id == "R-CANCEL-04").verdict == "VIOLATED"


def test_expired_exception_is_ignored() -> None:
    fs = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    assert waivers_for(fs, exceptions(), date(2026, 11, 1)) == []


def test_exception_missing_required_field_is_invalid() -> None:
    fs = evaluate(co.SPEC, co.RESTORE_ADMIN_ONLY, co.DOMAIN)
    broken = load_policy_exceptions(
        {"exceptions": [{"id": "EXC-2", "rule": "R-CANCEL-04", "reason": "x"}]}
    )
    assert broken.exceptions == []
    assert waivers_for(fs, broken, date(2026, 9, 22)) == []


def test_exception_does_not_waive_unknown_or_other_rules() -> None:
    fs = evaluate(co.SPEC, co.code_ir([co.P_FORBIDDEN, co.P_CONFLICT]), co.DOMAIN)  # 04 は UNKNOWN
    assert waivers_for(fs, exceptions(), date(2026, 9, 22)) == []
    fs2 = evaluate(co.SPEC, co.CONFLICT_400, co.DOMAIN)  # 違反は R-CANCEL-02
    assert waivers_for(fs2, exceptions(), date(2026, 9, 22)) == []


def test_path_target_matches_policy_findings() -> None:
    from speclino.domain.finding import Finding, Findings

    f = Finding(
        id="F-policy",
        requirement_id="MAKE-001:services/Makefile",
        behavior_id="policy",
        verdict="VIOLATED",
        category="missing_effect",
        severity="high",
        message="test が空",
        claim_scope="command_invocation",
    )
    fs = Findings(findings=[f])
    exc = exceptions(rule="MAKE-001", target={"paths": ["services/*"]})
    assert len(waivers_for(fs, exc, date(2026, 9, 22))) == 1
    exc2 = exceptions(rule="MAKE-001", target={"paths": ["web/*"]})
    assert waivers_for(fs, exc2, date(2026, 9, 22)) == []
