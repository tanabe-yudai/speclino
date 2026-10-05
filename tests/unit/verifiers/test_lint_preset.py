"""lint.preset の検証器テスト（S69）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from speclino.domain.finding import Finding
from speclino.verifiers.lint_preset import LintPresetVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import MakeRule, WriteFiles

RUFF_OK = '[tool.ruff.lint]\nselect = ["E", "F"]\nextend-select = ["I", "UP"]\nignore = ["E501"]\n'
CI_OK = "jobs:\n  lint:\n    steps:\n      - run: uv run ruff check .\n"
RUFF_PARAMS: dict[str, Any] = {
    "tool": "ruff",
    "config_path": "pyproject.toml",
    "required_rules": ["E", "I001", "UP", "F401"],
    "ci_glob": ".github/workflows/*.yml",
    "ci_command_pattern": r"ruff check",
}


def _run(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    files: dict[str, str],
    params: dict[str, Any],
    on_missing: str = "violation",
) -> list[Finding]:
    write_files(tmp_path, files)
    rule = make_rule(
        id="LINT-001",
        verifier_id="lint.preset",
        paths=["pyproject.toml"],
        params=params,
        on_missing_input=on_missing,
    )
    findings = LintPresetVerifier().run(rule, DirectorySnapshot(root=tmp_path))
    for f in findings:
        Finding.model_validate(f.model_dump())
        assert f.verdict != "REVIEW"
    return findings


def test_ruff_config_and_ci_are_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": CI_OK},
        RUFF_PARAMS,
    )
    assert [f.requirement_id for f in findings] == [
        "LINT-001:pyproject.toml",
        "LINT-001:.github/workflows/*.yml",
    ]
    assert {f.verdict for f in findings} == {"VERIFIED_IN_SCOPE"}


def test_ruff_prefix_and_ignore_specificity(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {"tool": "ruff", "config_path": "pyproject.toml"}
    # select "E" は E501 を含むが ignore "E501" が具体的で勝つ
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK},
        {**params, "required_rules": ["E501"]},
    )
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    # select "E" は E701 を含む。"B" は未選択
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK},
        {**params, "required_rules": ["E701", "B"]},
    )
    assert findings[0].verdict == "VIOLATED"
    assert "B" in (findings[0].actual or "") and "E701" not in (findings[0].actual or "")


def test_ruff_default_select_and_ruff_toml(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"ruff.toml": '[lint]\nextend-select = ["I"]\n'},
        {"tool": "ruff", "config_path": "ruff.toml", "required_rules": ["F401", "I001"]},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_eslint_json_rules(tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles) -> None:
    cfg = json.dumps({"rules": {"no-var": "error", "eqeqeq": ["warn", "always"], "semi": "off"}})
    base = {"tool": "eslint", "config_path": ".eslintrc.json"}
    ok = _run(
        tmp_path, make_rule, write_files, {".eslintrc.json": cfg},
        {**base, "required_rules": ["no-var", "eqeqeq"]},
    )  # fmt: skip
    assert ok[0].verdict == "VERIFIED_IN_SCOPE"
    bad = _run(
        tmp_path, make_rule, write_files, {".eslintrc.json": cfg},
        {**base, "required_rules": ["semi", "quotes"]},
    )  # fmt: skip
    assert bad[0].verdict == "VIOLATED"
    assert "semi" in (bad[0].actual or "") and "quotes" in (bad[0].actual or "")


def test_eslint_flat_config_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"eslint.config.js": "export default []"},
        {"tool": "eslint", "config_path": "eslint.config.js", "required_rules": ["no-var"]},
    )
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_missing_config_follows_on_missing_input(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {"tool": "ruff", "config_path": "pyproject.toml", "required_rules": ["F"]}
    v = _run(tmp_path, make_rule, write_files, {}, params, "violation")
    assert (v[0].verdict, v[0].category) == ("VIOLATED", "missing_effect")
    u = _run(tmp_path, make_rule, write_files, {}, params, "unknown")
    assert (u[0].verdict, u[0].category) == ("UNKNOWN", "unsupported_analysis")
    assert _run(tmp_path, make_rule, write_files, {}, params, "skipped") == []


def test_unparsable_config_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": "[tool.ruff\n"},
        {"tool": "ruff", "config_path": "pyproject.toml", "required_rules": ["F"]},
    )
    assert findings[0].verdict == "UNKNOWN"
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {".eslintrc.json": "{oops"},
        {"tool": "eslint", "config_path": ".eslintrc.json"},
    )
    assert findings[0].verdict == "UNKNOWN"


def test_ci_variants(tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles) -> None:
    params = {**RUFF_PARAMS, "required_rules": []}

    def ci(content: str, extra: dict[str, str] | None = None) -> Finding:
        for old in (tmp_path / ".github").glob("**/*.yml"):
            old.unlink()
        files = {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": content, **(extra or {})}
        return _run(tmp_path, make_rule, write_files, files, params)[1]

    assert ci("jobs:\n  a:\n    steps:\n      - run: make test\n").verdict == "VIOLATED"
    cond = "jobs:\n  a:\n    steps:\n      - if: github.ref == 'x'\n        run: ruff check .\n"
    assert ci(cond).verdict == "UNKNOWN"
    tolerated = (
        "jobs:\n  a:\n    steps:\n      - run: ruff check .\n        continue-on-error: true\n"
    )
    assert ci(tolerated).verdict == "VIOLATED"
    assert ci("jobs: [", {".github/workflows/ok.yml": CI_OK}).verdict == "VERIFIED_IN_SCOPE"
    assert ci("jobs: [").verdict == "UNKNOWN"


def test_missing_ci_follows_on_missing_input(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {**RUFF_PARAMS, "required_rules": ["B"]}
    findings = _run(
        tmp_path, make_rule, write_files, {"pyproject.toml": RUFF_OK}, params, "unknown"
    )
    assert [f.verdict for f in findings] == ["VIOLATED", "UNKNOWN"]


def test_invalid_params_raise_value_error(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {"tool": "pylint", "config_path": "x"})
    with pytest.raises(ValueError):
        _run(
            tmp_path,
            make_rule,
            write_files,
            {},
            {"tool": "ruff", "config_path": "x", "ci_command_pattern": "a"},
        )
    with pytest.raises(ValueError):
        _run(
            tmp_path,
            make_rule,
            write_files,
            {},
            {"tool": "ruff", "config_path": "x", "ci_glob": "a", "ci_command_pattern": "(["},
        )


def test_catastrophic_ci_pattern_times_out_to_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    ci = "jobs:\n  a:\n    steps:\n      - run: " + "a" * 40 + "!\n"
    params = {**RUFF_PARAMS, "required_rules": [], "ci_command_pattern": "(a|aa)+$"}
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": ci},
        params,
    )
    assert findings[1].verdict == "UNKNOWN"
    assert "タイムアウト" in findings[1].message


def test_too_long_pattern_is_rejected(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {**RUFF_PARAMS, "ci_command_pattern": "a" * 257}
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, params)
