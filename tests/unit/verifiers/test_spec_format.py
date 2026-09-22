"""SPEC-FMT-001 (spec.required_sections) の検証器テスト（AC-002）。"""

from __future__ import annotations

from pathlib import Path

from speclino.domain.finding import Finding
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_format import SpecFormatVerifier

from .conftest import REPO_ROOT, MakeRule, WriteFiles

PARAMS = {
    "id_pattern": r"^R-[A-Z]+-\d+[a-z]?$",
    "required_sections": ["## 前提", "## 要求", "## 観測境界"],
}

VALID_SPEC = (
    "---\n"
    "id: SPEC-DEMO\n"
    "version: '0.1'\n"
    "owner: alice\n"
    "---\n\n"
    "# デモ仕様\n\n"
    "## 前提\n\n"
    "- 何かの前提\n\n"
    "## 要求\n\n"
    "### R-DEMO-01\n\n"
    "- 条件と期待する結果\n\n"
    "## 観測境界\n\n"
    "- 届く範囲\n"
)


def _run(tmp_path: Path, make_rule: MakeRule) -> list[Finding]:
    rule = make_rule(
        id="SPEC-FMT-001",
        verifier_id="spec.required_sections",
        paths=["specs/*.md"],
        params=PARAMS,
        on_missing_input="unknown",
    )
    snapshot = DirectorySnapshot(root=tmp_path)
    return SpecFormatVerifier().run(rule, snapshot)


def test_valid_spec_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(tmp_path, {"specs/demo.md": VALID_SPEC})
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.verdict == "VERIFIED_IN_SCOPE"
    assert finding.category == "conformant"
    assert finding.requirement_id == "SPEC-FMT-001:specs/demo.md"
    Finding.model_validate(finding.model_dump())


def test_missing_required_section_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    spec = VALID_SPEC.replace("## 観測境界\n\n- 届く範囲\n", "")
    write_files(tmp_path, {"specs/demo.md": spec})
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "観測境界" in findings[0].actual


def test_no_requirement_id_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    spec = VALID_SPEC.replace("### R-DEMO-01\n\n", "")
    write_files(tmp_path, {"specs/demo.md": spec})
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "id" in findings[0].actual.lower()


def test_duplicate_requirement_id_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    spec = VALID_SPEC.replace(
        "### R-DEMO-01\n\n- 条件と期待する結果\n\n",
        "### R-DEMO-01\n\n- 条件と期待する結果\n\n### R-DEMO-01\n\n- 別の要求\n\n",
    )
    write_files(tmp_path, {"specs/demo.md": spec})
    findings = _run(tmp_path, make_rule)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "constraint_mismatch"


def test_finding_id_is_deterministic(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(tmp_path, {"specs/demo.md": VALID_SPEC})
    first = _run(tmp_path, make_rule)
    second = _run(tmp_path, make_rule)
    assert first[0].id == second[0].id


def test_repository_template_is_verified(make_rule: MakeRule) -> None:
    rule = make_rule(
        id="SPEC-FMT-001",
        verifier_id="spec.required_sections",
        paths=["specs/*.md"],
        params=PARAMS,
        on_missing_input="unknown",
    )
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    findings = SpecFormatVerifier().run(rule, snapshot)
    # specs/_template.md と自己検証の仕様（specs/SPEC-*.md）がすべて適合する
    assert len(findings) >= 2
    for f in findings:
        assert f.verdict == "VERIFIED_IN_SCOPE", (f.requirement_id, f.message)
