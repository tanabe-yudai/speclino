import pytest
from pydantic import ValidationError

from speclino.domain.assessment import AssessmentKey
from speclino.domain.finding import Finding


def test_key_is_stable_string() -> None:
    k = AssessmentKey(
        policy_revision=3,
        rule_id="ERRMSG-002",
        unit_hash="sha256:" + "a" * 64,
        model="m",
        prompt_version="1",
    )
    assert k.as_str() == AssessmentKey.model_validate(k.model_dump()).as_str()


def test_finding_accepts_assessment_outcome() -> None:
    assert "assessment_outcome" in Finding.model_fields


def test_finding_assessment_outcome_validated() -> None:
    base = {
        "id": "f1",
        "requirement_id": "R1",
        "behavior_id": "b",
        "verdict": "REVIEW",
        "category": "missing_finding",
        "severity": "info",
        "message": "m",
        "claim_scope": "service_outcome",
    }
    ok = Finding.model_validate({**base, "assessment_outcome": "issue"})
    assert ok.assessment_outcome == "issue"
    with pytest.raises(ValidationError):
        Finding.model_validate({**base, "assessment_outcome": "bogus"})
