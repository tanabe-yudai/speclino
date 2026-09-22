"""MAKE-001 (makefile.required_targets) の検証器テスト（AC-004）。"""

from __future__ import annotations

from pathlib import Path

from speclino.domain.finding import Finding
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import REPO_ROOT, MakeRule, WriteFiles

PARAMS = {"targets": ["setup", "test", "lint"], "trivial": ["true", ":", "@true", "echo *"]}


def _run(tmp_path: Path, make_rule: MakeRule) -> list[Finding]:
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=PARAMS,
    )
    snapshot = DirectorySnapshot(root=tmp_path)
    return MakeTargetsVerifier().run(rule, snapshot)


def test_normal_makefile_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "Makefile": "setup:\n\tuv sync\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n",
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.verdict == "VERIFIED_IN_SCOPE"
    assert finding.category == "conformant"
    assert finding.requirement_id == "MAKE-001"
    Finding.model_validate(finding.model_dump())


def test_empty_recipe_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": "setup:\n\tuv sync\n\ntest:\n\nlint:\n\truff check .\n"},
    )
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "test" in findings[0].actual


def test_trivial_inline_recipe_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": "setup:\n\tuv sync\n\ntest: ; @true\n\nlint:\n\truff check .\n"},
    )
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"


def test_undefined_target_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": "setup:\n\tuv sync\n\nlint:\n\truff check .\n"},
    )
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert "未定義" in findings[0].actual


def test_include_makes_missing_target_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": ("include common.mk\n\nsetup:\n\tuv sync\n\nlint:\n\truff check .\n")},
    )
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_dependency_with_real_recipe_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "Makefile": (
                "setup:\n\tuv sync\n\ntest: unit\n\nunit:\n\tpytest -v\n\nlint:\n\truff check .\n"
            )
        },
    )
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"
    assert findings[0].category == "conformant"


def test_finding_id_is_deterministic(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {"Makefile": "setup:\n\tuv sync\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n"},
    )
    first = _run(tmp_path, make_rule)
    second = _run(tmp_path, make_rule)
    assert first[0].id == second[0].id


def test_this_repository_makefile_is_verified(make_rule: MakeRule) -> None:
    rule = make_rule(
        id="MAKE-001",
        verifier_id="makefile.required_targets",
        paths=["Makefile"],
        params=PARAMS,
    )
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    findings = MakeTargetsVerifier().run(rule, snapshot)
    assert len(findings) == 1
    assert findings[0].verdict == "VERIFIED_IN_SCOPE", findings[0].message
