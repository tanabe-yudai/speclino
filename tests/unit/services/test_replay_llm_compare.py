"""replay の LLM 支援の比較（S70）。両方向に比べ、区切りの衝突しないキーを読む。"""

from __future__ import annotations

from speclino.domain.assessment import AssessmentKey
from speclino.domain.finding import Finding, Findings
from speclino.services.llm_policies import key_str
from speclino.services.replay_service import _models_by_rule, compare_llm_findings


def _f(fid: str, outcome: str = "issue") -> Finding:
    return Finding(
        id=fid,
        requirement_id="R:app.py",
        behavior_id="policy:llm.checklist",
        verdict="REVIEW",
        category="potential_violation",
        severity="high",
        message="m",
        claim_scope="persisted_state",
        assessment_outcome=outcome,  # type: ignore[arg-type]
    )


def test_compare_is_bidirectional() -> None:
    saved = Findings(findings=[_f("a"), _f("b")])
    fresh = Findings(findings=[_f("a", "no_issue"), _f("c")])
    diffs = compare_llm_findings(saved, fresh)
    assert any("（b）" in d and "再現できない" in d for d in diffs)
    assert any("（c）" in d and "増えた" in d for d in diffs)
    assert any("（a）" in d and "no_issue" in d for d in diffs)
    assert compare_llm_findings(saved, saved) == []


def test_models_by_rule_reads_json_keys_with_separators_in_fields() -> None:
    key = AssessmentKey(
        policy_revision=1,
        rule_id="R|odd@project:abc",
        unit_hash="sha256:" + "a" * 64,
        model="m|x",
        prompt_version="v1+abc",
    )
    assert _models_by_rule([key_str(key), "壊れたキー"]) == {"R|odd@project:abc": "m|x"}
