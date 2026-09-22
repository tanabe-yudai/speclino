"""CI-001 (ci.required_conditions) の検証器テスト（S36）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from speclino.domain.finding import Finding
from speclino.verifiers.ci_required import CiRequiredVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import REPO_ROOT, MakeRule

PARAMS: dict[str, Any] = {
    "require_pull_request_trigger": True,
    "required_steps": ["make lint", "make test"],
    "forbid_events": ["pull_request_target"],
    "require_read_permissions": True,
    "allowed_write_permissions": ["id-token"],
}

VALID_WORKFLOW = """\
name: CI

on: [push, pull_request]

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make lint
      - run: make test
"""


def _run(
    tmp_path: Path, make_rule: MakeRule, workflow: str, params: dict[str, Any] = PARAMS
) -> list[Finding]:
    target = tmp_path / ".github" / "workflows" / "ci.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(workflow, encoding="utf-8")
    rule = make_rule(
        id="CI-001",
        verifier_id="ci.required_conditions",
        paths=[".github/workflows/ci.yml"],
        params=params,
        on_missing_input="violation",
    )
    snapshot = DirectorySnapshot(root=tmp_path)
    return CiRequiredVerifier().run(rule, snapshot)


def test_valid_workflow_is_verified(tmp_path: Path, make_rule: MakeRule) -> None:
    findings = _run(tmp_path, make_rule, VALID_WORKFLOW)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.verdict == "VERIFIED_IN_SCOPE"
    assert finding.category == "conformant"
    assert finding.requirement_id == "CI-001:.github/workflows/ci.yml"
    Finding.model_validate(finding.model_dump())


def test_missing_pull_request_trigger_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace("on: [push, pull_request]", "on: [push]")
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "pull_request" in findings[0].actual


def test_missing_required_step_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace("      - run: make test\n", "")
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "make test" in findings[0].actual


def test_forbidden_event_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "on: [push, pull_request]", "on: [push, pull_request, pull_request_target]"
    )
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "forbidden_effect"
    assert "pull_request_target" in findings[0].actual


def test_missing_permissions_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace("permissions:\n  contents: read\n\n", "")
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "constraint_mismatch"
    assert "permissions" in findings[0].actual


def test_write_permission_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace("contents: read", "contents: write")
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "constraint_mismatch"
    assert "contents" in findings[0].actual


def test_allowed_write_permission_is_verified(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "permissions:\n  contents: read\n", "permissions:\n  contents: read\n  id-token: write\n"
    )
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"
    assert findings[0].category == "conformant"


def test_reusable_workflow_call_is_unknown(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = """\
name: CI

on: [push, pull_request]

permissions:
  contents: read

jobs:
  python:
    uses: ./.github/workflows/_reusable.yml
"""
    findings = _run(tmp_path, make_rule, workflow)
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_require_environment_protection_is_unknown(tmp_path: Path, make_rule: MakeRule) -> None:
    params = {**PARAMS, "require_environment_protection": True}
    findings = _run(tmp_path, make_rule, VALID_WORKFLOW, params)
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
    assert "外部設定を取得できないため確認不能" in findings[0].message


def test_determinate_violation_takes_priority_over_environment_protection(
    tmp_path: Path, make_rule: MakeRule
) -> None:
    """他に決定的な VIOLATED があれば、外部状態の確認不能で覆い隠さない。"""
    params = {**PARAMS, "require_environment_protection": True}
    workflow = VALID_WORKFLOW.replace("on: [push, pull_request]", "on: [push]")
    findings = _run(tmp_path, make_rule, workflow, params)
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"


def test_finding_id_is_deterministic(tmp_path: Path, make_rule: MakeRule) -> None:
    first = _run(tmp_path, make_rule, VALID_WORKFLOW)
    second = _run(tmp_path, make_rule, VALID_WORKFLOW)
    assert first[0].id == second[0].id


def test_repository_ci_yml_is_verified(make_rule: MakeRule) -> None:
    rule = make_rule(
        id="CI-001",
        verifier_id="ci.required_conditions",
        paths=[".github/workflows/ci.yml"],
        params=PARAMS,
        on_missing_input="violation",
    )
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    findings = CiRequiredVerifier().run(rule, snapshot)
    assert len(findings) == 1
    assert findings[0].verdict == "VERIFIED_IN_SCOPE", findings[0].message


def test_deploy_workflow_is_not_targeted(make_rule: MakeRule) -> None:
    """deploy.yml は applies_to に含めない（push のみで pull_request トリガーが無いため）。"""
    rule = make_rule(
        id="CI-001",
        verifier_id="ci.required_conditions",
        paths=[".github/workflows/ci.yml"],
        params=PARAMS,
        on_missing_input="violation",
    )
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    findings = CiRequiredVerifier().run(rule, snapshot)
    assert all("deploy.yml" not in f.requirement_id for f in findings)


# codex レビュー指摘の回帰試験: 「実行しない・失敗を無視する」記述を VERIFIED にしない


def test_substring_match_is_not_enough(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace("- run: make lint\n", "- run: echo make lint\n")
    finding = _run(tmp_path, make_rule, workflow)[0]
    assert (finding.verdict, finding.category) == ("VIOLATED", "missing_effect")
    assert "make lint" in finding.message


def test_multiline_run_matches_by_whole_line(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "      - run: make lint\n      - run: make test\n",
        "      - run: |\n          set -e\n          make lint\n          make test\n",
    )
    assert _run(tmp_path, make_rule, workflow)[0].verdict == "VERIFIED_IN_SCOPE"


def test_conditional_required_step_is_unknown(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "      - run: make test\n", "      - run: make test\n        if: false\n"
    )
    finding = _run(tmp_path, make_rule, workflow)[0]
    assert (finding.verdict, finding.category) == ("UNKNOWN", "unsupported_analysis")
    assert "if:" in finding.message


def test_continue_on_error_required_step_is_violated(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "      - run: make test\n", "      - run: make test\n        continue-on-error: true\n"
    )
    finding = _run(tmp_path, make_rule, workflow)[0]
    assert (finding.verdict, finding.category) == ("VIOLATED", "constraint_mismatch")


def test_job_level_if_makes_required_step_unknown(tmp_path: Path, make_rule: MakeRule) -> None:
    workflow = VALID_WORKFLOW.replace(
        "    runs-on: ubuntu-latest\n",
        "    runs-on: ubuntu-latest\n    if: github.event_name == 'push'\n",
    )
    assert _run(tmp_path, make_rule, workflow)[0].verdict == "UNKNOWN"
