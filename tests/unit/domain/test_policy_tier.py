"""規約の段（S70）: tier と検査器 llm.checklist の対応、チェック項目のパラメーター。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import LlmChecklistParams


def _rule(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ERRMSG-NEXT",
        "version": "1",
        "level": "mandatory",
        "title": "エラーは次の行動を示す",
        "text": "利用者が次に何をすべきかを書く",
        "applies_to": {"paths": ["src/**/*.py"]},
        "on_missing_input": "unknown",
        "verifier": {
            "id": "llm.checklist",
            "version": "1",
            "params": {"items": [{"id": "next", "question": "次の行動を示すか"}]},
        },
    }
    base.update(over)
    return base


def test_existing_rules_default_to_deterministic() -> None:
    rule = PolicyRule.model_validate(
        _rule(verifier={"id": "make.targets", "version": "1", "params": {}})
    )
    assert rule.tier == "deterministic"


def test_llm_assisted_rule_is_valid_and_mandatory_keeps_a_verifier() -> None:
    rule = PolicyRule.model_validate(_rule(tier="llm_assisted"))
    assert rule.tier == "llm_assisted"
    assert rule.verifier_ref.id == "llm.checklist"


def test_llm_assisted_needs_the_checklist_verifier() -> None:
    with pytest.raises(ValidationError, match="llm.checklist"):
        PolicyRule.model_validate(
            _rule(tier="llm_assisted", verifier={"id": "make.targets", "version": "1"})
        )


def test_checklist_verifier_needs_llm_tier() -> None:
    with pytest.raises(ValidationError, match="llm_assisted"):
        PolicyRule.model_validate(_rule())


def test_checklist_params() -> None:
    params = LlmChecklistParams.model_validate(
        {"items": [{"id": "next", "question": "q"}], "unit_kind": "string_literal"}
    )
    assert params.paths == [] and params.max_units == 50
    with pytest.raises(ValidationError, match="重複"):
        LlmChecklistParams.model_validate(
            {
                "items": [{"id": "a", "question": "q"}, {"id": "a", "question": "r"}],
                "unit_kind": "function",
            }
        )
    with pytest.raises(ValidationError):
        LlmChecklistParams.model_validate({"items": [], "unit_kind": "function"})
    with pytest.raises(ValidationError):
        LlmChecklistParams.model_validate(
            {"items": [{"id": "a", "question": "q"}], "unit_kind": "function", "extra": 1}
        )


def test_llm_assisted_never_violates_on_missing_input() -> None:
    with pytest.raises(ValidationError, match="on_missing_input"):
        PolicyRule.model_validate(_rule(tier="llm_assisted", on_missing_input="violation"))
