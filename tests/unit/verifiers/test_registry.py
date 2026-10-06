"""VerifierRegistry: applies_to 不一致時の on_missing_input ごとの挙動。"""

from __future__ import annotations

from pathlib import Path

import pytest

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyExceptions, PolicyPackage, PolicyRule
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import MakeRule, WriteFiles

MAKE_PARAMS = {"targets": ["setup", "test", "lint"], "trivial": ["true", ":", "@true", "echo *"]}


def _empty_exceptions() -> PolicyExceptions:
    return PolicyExceptions(schema_version="0.1", exceptions=[])


def _package(rule: PolicyRule) -> PolicyPackage:
    return PolicyPackage(
        schema_version="0.1",
        id="pkg",
        version="1.0.0",
        status="active",
        owner="x",
        rules=[rule],
    )


def test_missing_input_violation(tmp_path: Path, make_rule: MakeRule) -> None:
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=MAKE_PARAMS,
        on_missing_input="violation",
    )
    registry = VerifierRegistry()
    findings = registry.run_package(
        _package(rule), _empty_exceptions(), DirectorySnapshot(root=tmp_path)
    )
    assert len(findings) == 1
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    Finding.model_validate(findings[0].model_dump())


def test_missing_input_unknown(tmp_path: Path, make_rule: MakeRule) -> None:
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=MAKE_PARAMS,
        on_missing_input="unknown",
    )
    registry = VerifierRegistry()
    findings = registry.run_package(
        _package(rule), _empty_exceptions(), DirectorySnapshot(root=tmp_path)
    )
    assert len(findings) == 1
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_missing_input_skipped(tmp_path: Path, make_rule: MakeRule) -> None:
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=MAKE_PARAMS,
        on_missing_input="skipped",
    )
    registry = VerifierRegistry()
    findings = registry.run_package(
        _package(rule), _empty_exceptions(), DirectorySnapshot(root=tmp_path)
    )
    assert findings == []


def test_matched_rule_delegates_to_verifier(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": "setup:\n\tuv sync\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n"},
    )
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=MAKE_PARAMS,
        on_missing_input="violation",
    )
    registry = VerifierRegistry()
    registry.register(MakeTargetsVerifier())
    findings = registry.run_package(
        _package(rule), _empty_exceptions(), DirectorySnapshot(root=tmp_path)
    )
    assert len(findings) == 1
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_matched_rule_without_registered_verifier_raises(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(tmp_path, {"Makefile": "setup:\n\tuv sync\n"})
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=MAKE_PARAMS,
        on_missing_input="violation",
    )
    registry = VerifierRegistry()
    with pytest.raises(LookupError):
        registry.run_package(_package(rule), _empty_exceptions(), DirectorySnapshot(root=tmp_path))


def test_policy_registry_registers_s69_verifiers() -> None:
    from speclino.services.check_pipeline import _policy_registry

    registry = _policy_registry()
    for verifier_id in (
        "api.error_format",
        "api.conventions",
        "naming.identifiers",
        "error_message.format",
        "lint.preset",
    ):
        assert registry.get(verifier_id) is not None


def test_executable_and_approvable_verifiers_are_the_same_set() -> None:
    """#114: 実行の登録と承認時の検証は verifiers/catalog.py の 1 つの一覧から作る。"""
    from speclino.services import policy_governance as gov
    from speclino.services.check_pipeline import _policy_registry
    from speclino.verifiers.catalog import KNOWN_VERIFIERS

    executable = _policy_registry().versions()
    assert executable == KNOWN_VERIFIERS == gov.KNOWN_VERIFIERS
    for verifier_id in (
        "makefile.required_targets",
        "api.conventions",
        "naming.identifiers",
        "error_message.format",
        "lint.preset",
        "llm.checklist",
    ):
        assert verifier_id in KNOWN_VERIFIERS, verifier_id


def test_every_executable_verifier_passes_approval_validation() -> None:
    from speclino.domain.policy import PolicyApplicability, PolicyVerifierRef
    from speclino.services import policy_governance as gov
    from speclino.services.check_pipeline import _policy_registry

    for verifier_id, version in _policy_registry().versions().items():
        llm = verifier_id == "llm.checklist"
        rule = PolicyRule(
            id="R-1",
            version="1",
            level="advisory",
            title="t",
            text="t",
            applies_to=PolicyApplicability(paths=["**/*.py"]),
            verifier=PolicyVerifierRef(id=verifier_id, version=version, params={}),
            on_missing_input="unknown",
            tier="llm_assisted" if llm else "deterministic",
        )
        gov.validate_package(_package(rule))  # 例外にならない
    # 登録の無い検証器・違う版は承認できない
    unknown = rule.model_copy(
        update={
            "verifier": PolicyVerifierRef(id="no.such", version="1", params={}),
            "tier": "deterministic",
        }
    )
    with pytest.raises(gov.InvalidPackage):
        gov.validate_package(_package(unknown))


def test_llm_scope_warnings_for_missing_or_broad_paths() -> None:
    """#114: LLM 支援の規約の送り先が無指定・広すぎるときは承認時に警告する（拒否はしない）。"""
    from speclino.domain.policy import PolicyApplicability, PolicyVerifierRef
    from speclino.services import policy_governance as gov

    def llm_rule(rule_id: str, applies: list[str], paths: list[str] | None) -> PolicyRule:
        params: dict[str, object] = {
            "items": [{"id": "i", "question": "q"}],
            "unit_kind": "function",
        }
        if paths is not None:
            params["paths"] = paths
        return PolicyRule(
            id=rule_id,
            version="1",
            level="mandatory",
            title="t",
            text="t",
            applies_to=PolicyApplicability(paths=applies),
            verifier=PolicyVerifierRef(id="llm.checklist", version="1", params=params),
            on_missing_input="unknown",
            tier="llm_assisted",
        )

    narrow = llm_rule("OK", ["app/api/*.py"], ["app/api/*.py"])
    assert gov.llm_scope_warnings(_package(narrow)) == []
    missing = gov.llm_scope_warnings(_package(llm_rule("NOPATHS", ["app/api/*.py"], None)))
    assert len(missing) == 1 and "params.paths" in missing[0]
    broad = gov.llm_scope_warnings(_package(llm_rule("BROAD", ["src/*.py"], ["**/*"])))
    assert len(broad) == 1 and "広すぎる" in broad[0] and "**/*" in broad[0]
    both = gov.llm_scope_warnings(_package(llm_rule("BOTH", ["**/*.py"], None)))
    assert len(both) == 2
    gov.validate_package(_package(llm_rule("BOTH", ["**/*.py"], None)))  # 拒否はしない


def test_string_literal_llm_rule_requires_call_names() -> None:
    """#114 裁定 37: 文字列の単位を送る規約は、送るリテラルを呼出しで限る。無ければ承認しない。"""
    from speclino.domain.policy import PolicyApplicability, PolicyVerifierRef
    from speclino.services import policy_governance as gov

    def rule(params: dict[str, object]) -> PolicyRule:
        return PolicyRule(
            id="MSG",
            version="1",
            level="mandatory",
            title="t",
            text="t",
            applies_to=PolicyApplicability(paths=["app/api/*.py"]),
            verifier=PolicyVerifierRef(id="llm.checklist", version="1", params=params),
            on_missing_input="unknown",
            tier="llm_assisted",
        )

    base = {"items": [{"id": "i", "question": "q"}], "unit_kind": "string_literal"}
    with pytest.raises(gov.InvalidPackage, match="call_names"):
        gov.validate_package(_package(rule(base)))
    gov.validate_package(_package(rule({**base, "call_names": ["HTTPException"]})))
    gov.validate_package(_package(rule({**base, "unit_kind": "function"})))
