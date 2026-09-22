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
