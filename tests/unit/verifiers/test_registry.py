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
        gov._validate(_package(rule))  # 例外にならない
    # 登録の無い検証器・違う版は承認できない
    unknown = rule.model_copy(
        update={
            "verifier": PolicyVerifierRef(id="no.such", version="1", params={}),
            "tier": "deterministic",
        }
    )
    with pytest.raises(gov.InvalidPackage):
        gov._validate(_package(unknown))
