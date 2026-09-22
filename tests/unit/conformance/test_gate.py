from __future__ import annotations

from speclino.conformance.gate import GatePolicy, aggregate_gate
from speclino.domain.finding import Finding, Findings, Verdict


def finding(req: str, verdict: Verdict, fid: str | None = None) -> Finding:
    return Finding(
        id=fid or f"F-{req}-{verdict}",
        requirement_id=req,
        behavior_id="b",
        verdict=verdict,
        category="conformant" if verdict == "VERIFIED_IN_SCOPE" else "missing_effect",
        severity="info" if verdict == "VERIFIED_IN_SCOPE" else "high",
        message="m",
        claim_scope="command_invocation",
    )


def test_empty_findings_fail_closed() -> None:
    assert aggregate_gate(Findings(), {}).status == "fail"


def test_all_verified_passes() -> None:
    fs = Findings(findings=[finding("R1", "VERIFIED_IN_SCOPE"), finding("R2", "VERIFIED_IN_SCOPE")])
    assert aggregate_gate(fs, {"R1": "mandatory", "R2": "mandatory"}).status == "pass"


def test_unknown_and_review_fail_by_default() -> None:
    for v in ("UNKNOWN", "REVIEW", "VIOLATED"):
        fs = Findings(findings=[finding("R1", "VERIFIED_IN_SCOPE"), finding("R2", v)])  # type: ignore[arg-type]
        res = aggregate_gate(fs, {"R1": "mandatory", "R2": "mandatory"})
        assert res.status == "fail" and res.blocking[0].requirement_id == "R2"


def test_advisory_rule_does_not_block_alone() -> None:
    fs = Findings(findings=[finding("R1", "VERIFIED_IN_SCOPE"), finding("A1", "VIOLATED")])
    res = aggregate_gate(fs, {"R1": "mandatory", "A1": "advisory"})
    assert res.status == "pass" and [f.requirement_id for f in res.advisory] == ["A1"]


def test_unlisted_rule_is_treated_as_mandatory() -> None:
    fs = Findings(findings=[finding("R1", "UNKNOWN")])
    assert aggregate_gate(fs, {}).status == "fail"


def test_waived_violation_passes_with_exceptions() -> None:
    fs = Findings(findings=[finding("R1", "VERIFIED_IN_SCOPE"), finding("R2", "VIOLATED", "F-x")])
    res = aggregate_gate(fs, {"R1": "mandatory", "R2": "mandatory"}, waived_ids=frozenset({"F-x"}))
    assert res.status == "pass_with_exceptions" and len(res.waived) == 1


def test_waiver_does_not_cover_unknown() -> None:
    fs = Findings(findings=[finding("R1", "UNKNOWN", "F-x")])
    assert aggregate_gate(fs, {"R1": "mandatory"}, waived_ids=frozenset({"F-x"})).status == "fail"


def test_custom_fail_on_only_violated() -> None:
    fs = Findings(findings=[finding("R1", "UNKNOWN")])
    res = aggregate_gate(fs, {"R1": "mandatory"}, GatePolicy(fail_on=("VIOLATED",)))
    assert res.status == "pass"
