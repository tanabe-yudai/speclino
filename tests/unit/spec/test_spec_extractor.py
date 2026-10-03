"""spec_extractor（S33）: 候補は検証を通り、曖昧箇所と語彙外の参照は Diagnostic に残る。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.spec import load_bindings, load_domain
from speclino.spec.spec_extractor import build_prompt, extract_spec, parse_proposal

ORDERS = Path(__file__).resolve().parents[3] / "examples" / "orders"
DOMAIN = load_domain(ORDERS / ".speclino" / "domain.yaml")
BINDINGS = load_bindings(ORDERS / ".speclino" / "bindings.yaml")
MARKDOWN = (ORDERS / "specs" / "orders-cancel.md").read_text(encoding="utf-8")

RESTORE_ONCE = {
    "id": "R-CANCEL-04",
    "requirement_id": "R-CANCEL-04",
    "title": "在庫復元を 1 回呼ぶ",
    "modality": "must",
    "claim_scope": "command_invocation",
    "applicability": {
        "op": "and",
        "args": [
            {
                "op": "or",
                "args": [
                    {"op": "ref", "path": "before.actor.is_admin"},
                    {
                        "op": "eq",
                        "left": {"op": "ref", "path": "before.actor.id"},
                        "right": {"op": "ref", "path": "before.order.owner_id"},
                    },
                ],
            },
            {
                "op": "eq",
                "left": {"op": "ref", "path": "before.order.status"},
                "right": {"op": "const", "type": "OrderStatus", "value": "PENDING"},
            },
        ],
    },
    "assertion": {
        "kind": "event_requirement",
        "event_kind": "call",
        "operation_or_target": "inventory_restore_command",
        "phase": "invoked",
        "count": {"operator": "eq", "value": 1},
    },
}


def _proposal(**overrides: object) -> str:
    base = {
        "rules": [RESTORE_ONCE],
        "unextracted": [{"requirement_id": "R-CANCEL-06", "reason": "通知先を語彙で表せない"}],
        "ambiguities": [
            {
                "requirement_id": "R-CANCEL-05",
                "phrase": "速やかに記録する",
                "question": "同期呼出しか、後続のジョブでもよいか",
            }
        ],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def test_prompt_contains_vocabulary_and_markdown() -> None:
    prompt = build_prompt(MARKDOWN, "cancel_order", DOMAIN, BINDINGS)
    assert "inventory_restore_command" in prompt and "OrderStatus" in prompt
    assert "R-CANCEL-04" in prompt and "ambiguities" in prompt


async def test_candidate_validates_and_keeps_ambiguities_as_diagnostics() -> None:
    llm = ScriptedJsonLlm(_proposal())
    result = await extract_spec(
        MARKDOWN, "cancel_order", DOMAIN, BINDINGS, llm, spec_path="specs/orders-cancel.md"
    )
    assert llm.schemas and "rules" in llm.schemas[0]["properties"]
    assert (result.input_tokens, result.output_tokens) == llm.usage, "費用ガード用に使用量を返す"
    assert result.errors == []
    [rule] = result.spec.rules
    assert rule.approval_ref is None and result.spec.approvals == []
    assert rule.source_refs[0].path == "specs/orders-cancel.md"
    assert result.spec.unextracted == ["R-CANCEL-06"]
    codes = {d.code for d in result.warnings}
    assert {"W_AMBIGUOUS", "W_UNEXTRACTED", "W_UNAPPROVED"} <= codes
    ambiguous = next(d for d in result.warnings if d.code == "W_AMBIGUOUS")
    assert ambiguous.rule_id == "R-CANCEL-05" and "速やかに" in ambiguous.message


def test_hallucinated_variable_is_an_error() -> None:
    bad = json.loads(_proposal())
    bad["rules"][0]["applicability"] = {"op": "ref", "path": "before.actor.is_manager"}
    result = parse_proposal(json.dumps(bad), "cancel_order", MARKDOWN, DOMAIN, BINDINGS)
    assert any(d.code == "E_REF_UNDEFINED" for d in result.errors)


def test_malformed_output_is_reported_not_raised() -> None:
    result = parse_proposal("not json", "cancel_order", MARKDOWN, DOMAIN, BINDINGS)
    assert result.spec.rules == [] and result.errors[0].code == "E_CANDIDATE_INVALID"
    invalid_rule = json.loads(_proposal())
    invalid_rule["rules"][0]["assertion"] = {"kind": "expression"}  # expr が無い
    result = parse_proposal(json.dumps(invalid_rule), "cancel_order", MARKDOWN, DOMAIN, BINDINGS)
    assert result.errors[0].code == "E_CANDIDATE_INVALID"


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")),
    reason="Gemini の認証情報が無い",
)
async def test_live_gemini_extracts_candidate_rules() -> None:
    from speclino.adapters.llm.gemini_json import GeminiJsonLlm

    result = await extract_spec(
        MARKDOWN, "cancel_order", DOMAIN, BINDINGS, GeminiJsonLlm("gemini-3.8-flash")
    )
    assert result.errors == [], [d.message for d in result.errors]
    assert len(result.spec.rules) >= 5
