"""提案の許可リストと診断（S73、設計 C4・C5）。LLM・DB を使わない。"""

from __future__ import annotations

import json

from speclino.services.knowledge_chat_proposal import (
    build_proposal,
    extract_proposal,
    response_schema,
    text_hash,
)
from tests.unit.services.suggest_samples import BAD, FULL

BASE = text_hash("")


def test_text_hash_is_sha256_of_utf8() -> None:
    import hashlib

    assert text_hash("あ") == "sha256:" + hashlib.sha256("あ".encode()).hexdigest()


def test_spec_allowlist_drops_everything_but_markdown() -> None:
    raw, dropped = extract_proposal(
        "spec",
        {
            "markdown": "# x",
            "name": "evil",
            "scope": {"workspace": True},
            "domain": {},
            "legacy_ir": [],
            "compiled": {},
            "approved": True,
        },
    )
    assert raw == {"markdown": "# x"}
    assert dropped == 6


def test_instructions_and_skill_allowlist() -> None:
    raw, _ = extract_proposal("instructions", {"body": "b", "target": "claude_md"})
    assert raw == {"body": "b"}
    raw, _ = extract_proposal(
        "skill",
        {
            "skill_md": "---\nname: a\n---\n",
            "files": [{"path": "ref.md", "body": "r"}, {"path": 3, "body": "x"}],
            "scope": {"workspace": True},
        },
    )
    assert raw == {"skill_md": "---\nname: a\n---\n", "files": {"ref.md": "r"}}


def test_policy_allowlist_keeps_only_rule_definition() -> None:
    raw, dropped = extract_proposal(
        "policy",
        {
            "rule": {
                "id": "API-1",
                "title": "t",
                "text": "x",
                "level": "advisory",
                "tier": "deterministic",
                "verifier": {
                    "id": "make.targets",
                    "version": "1",
                    "params_json": '{"targets": ["test"]}',
                    "approved_by": "me",
                },
                "applies_to": {"paths": ["Makefile"], "languages": [], "owner": "x"},
                "exceptions_allowed": False,
                "approver": "me",
            }
        },
    )
    assert raw == {
        "rule": {
            "id": "API-1",
            "title": "t",
            "text": "x",
            "level": "advisory",
            "tier": "deterministic",
            "verifier": {"id": "make.targets", "version": "1", "params": {"targets": ["test"]}},
            "applies_to": {"paths": ["Makefile"], "languages": []},
        }
    }
    assert dropped >= 4


def test_no_proposal_is_none() -> None:
    assert extract_proposal("spec", None) == (None, 0)
    assert extract_proposal("spec", {"name": "x"})[0] is None


async def test_spec_proposal_is_compiled_for_diagnostics() -> None:
    good = await build_proposal("spec", {"markdown": FULL}, editor_text="", base_hash=BASE)
    assert good is not None and good.valid and good.raw_proposal == {"markdown": FULL}
    bad = await build_proposal("spec", {"markdown": BAD}, editor_text="", base_hash=BASE)
    assert bad is not None and not bad.valid
    assert any(d.code == "E_GUARD_SYNTAX" and d.severity == "error" for d in bad.diagnostics)
    assert bad.base_hash == BASE


async def test_invalid_skill_is_returned_with_diagnostics() -> None:
    p = await build_proposal(
        "skill",
        {"skill_md": "x", "files": [{"path": "../evil", "body": "x"}]},
        editor_text="",
        base_hash=BASE,
    )
    assert p is not None and not p.valid
    assert p.raw_proposal["files"] == {"../evil": "x"}
    assert p.diagnostics and all(d.severity == "error" for d in p.diagnostics)


async def test_instructions_marker_is_an_error() -> None:
    p = await build_proposal(
        "instructions", {"body": "<!-- speclino:begin -->"}, editor_text="", base_hash=BASE
    )
    assert p is not None and not p.valid


async def test_policy_rule_is_checked_against_the_catalog() -> None:
    base_rule = json.dumps({"id": "R-1", "version": "3", "on_missing_input": "skipped"})
    ok = await build_proposal(
        "policy",
        {"rule": {"id": "R-1", "title": "t", "text": "x", "level": "advisory"}},
        editor_text=base_rule,
        base_hash=BASE,
    )
    assert ok is not None and ok.valid, ok
    unknown = await build_proposal(
        "policy",
        {
            "rule": {
                "id": "R-1",
                "title": "t",
                "text": "x",
                "level": "mandatory",
                "verifier": {"id": "no.such", "version": "1", "params_json": "{}"},
            }
        },
        editor_text="",
        base_hash=BASE,
    )
    assert unknown is not None and not unknown.valid
    assert any("no.such" in d.message for d in unknown.diagnostics)
    mandatory_without_verifier = await build_proposal(
        "policy",
        {"rule": {"id": "R-2", "title": "t", "text": "x", "level": "mandatory"}},
        editor_text="",
        base_hash=BASE,
    )
    assert mandatory_without_verifier is not None and not mandatory_without_verifier.valid


async def test_dropped_fields_are_reported_without_their_names() -> None:
    p = await build_proposal(
        "instructions", {"body": "ok", "scope_override": "x"}, editor_text="", base_hash=BASE
    )
    assert p is not None and p.valid
    (info,) = [d for d in p.diagnostics if d.code == "I_FIELDS_DROPPED"]
    assert info.severity == "info" and "scope_override" not in info.message


def test_response_schema_has_explanation_and_kind_specific_proposal() -> None:
    for kind, field in (
        ("spec", "markdown"),
        ("skill", "skill_md"),
        ("instructions", "body"),
        ("policy", "rule"),
    ):
        schema = response_schema(kind)  # type: ignore[arg-type]
        assert schema["required"] == ["explanation"]
        assert field in schema["properties"]["proposal"]["properties"]
        text = json.dumps(schema)
        for banned in ('"scope"', '"name"', '"target"', '"domain"', '"legacy_ir"', '"compiled"'):
            assert banned not in text


def test_name_is_allowed_only_for_new_artifacts_and_valid_names() -> None:
    data = {"markdown": "# x", "name": "order-checkout"}
    assert extract_proposal("spec", data) == ({"markdown": "# x"}, 1)
    assert extract_proposal("spec", data, propose_name=True) == (data, 0)
    for bad in ("Order", "-x", "a_b", "a" * 65, 3, "valid-name\n"):
        raw, dropped = extract_proposal(
            "instructions", {"body": "b", "name": bad}, propose_name=True
        )
        assert raw == {"body": "b"} and dropped == 1, bad
    # 名前だけの提案は返さない。規約には名前の欄が無い
    assert extract_proposal("skill", {"name": "a"}, propose_name=True) == (None, 0)
    raw, dropped = extract_proposal(
        "policy",
        {"rule": {"id": "R", "title": "t", "text": "x", "level": "advisory"}, "name": "a"},
        propose_name=True,
    )
    assert raw is not None and "name" not in raw and dropped == 1


async def test_proposed_name_does_not_break_content_diagnostics() -> None:
    p = await build_proposal(
        "skill",
        {"skill_md": "---\nname: a\ndescription: d\n---\n", "name": "my-skill"},
        editor_text="",
        base_hash=BASE,
        propose_name=True,
    )
    assert p is not None and p.valid, p
    assert p.raw_proposal["name"] == "my-skill"


def test_response_schema_has_name_only_when_proposing() -> None:
    for kind in ("spec", "skill", "instructions"):
        props = response_schema(kind, propose_name=True)["properties"]["proposal"]["properties"]  # type: ignore[arg-type]
        assert "name" in props
    policy = response_schema("policy", propose_name=True)["properties"]["proposal"]["properties"]
    assert "name" not in policy
