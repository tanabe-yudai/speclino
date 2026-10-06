"""lint.preset の検証器テスト（S69）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
        {"tool": "eslint", "config_path": ".eslintrc.json", "required_rules": ["semi"]},
    )
    assert findings[0].verdict == "UNKNOWN"


def test_ci_variants(tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles) -> None:
    params = {**RUFF_PARAMS, "required_rules": []}

    def ci(content: str, extra: dict[str, str] | None = None) -> Finding:
        for old in (tmp_path / ".github").glob("**/*.yml"):
            old.unlink()
        files = {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": content, **(extra or {})}
        return _run(tmp_path, make_rule, write_files, files, params)[0]

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


def _assert_invalid_params(findings: list[Finding]) -> None:
    assert len(findings) == 1
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
    assert findings[0].requirement_id == "LINT-001"
    assert "パラメーターが不正" in findings[0].message


def test_invalid_params_are_unknown_not_raised(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for params in (
        {"tool": "pylint", "config_path": "x", "required_rules": ["F"]},
        {"tool": "ruff", "config_path": "x", "ci_command_pattern": "a"},  # ci_glob なし
        {"tool": "ruff", "config_path": "x", "ci_glob": "a", "ci_command_pattern": "(["},
        {"tool": "ruff", "config_path": "x"},  # 検査項目なし
        {"tool": "ruff", "config_path": "x", "required_rules": ["F"], "typo": 1},
        {**RUFF_PARAMS, "ci_command_pattern": "a" * 257},
    ):
        _assert_invalid_params(_run(tmp_path, make_rule, write_files, {}, params))


def _ruff(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    toml: str,
    required: list[str],
) -> Finding:
    params = {"tool": "ruff", "config_path": "pyproject.toml", "required_rules": required}
    return _run(tmp_path, make_rule, write_files, {"pyproject.toml": toml}, params)[0]


def test_ruff_prefix_requires_digit_after_selector(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    # 既定の select（F を含む）は別のリンター FBT を含まない
    f = _ruff(tmp_path, make_rule, write_files, "[tool.ruff]\n", ["FBT001"])
    assert f.verdict == "VIOLATED"
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["B"]\n', ["BLE001"])
    assert f.verdict == "VIOLATED"
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["E"]\n', ["EM101"])
    assert f.verdict == "VIOLATED"
    # 数字が続くなら接頭辞、PL は Pylint の子リンターを含む
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["PL"]\n', ["PLR0913"])
    assert f.verdict == "VERIFIED_IN_SCOPE"
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["B"]\n', ["B006"])
    assert f.verdict == "VERIFIED_IN_SCOPE"


def test_ruff_all_has_lowest_specificity(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    f = _ruff(
        tmp_path,
        make_rule,
        write_files,
        '[tool.ruff.lint]\nselect = ["ALL"]\nignore = ["E"]\n',
        ["E501"],
    )
    assert f.verdict == "VIOLATED"
    f = _ruff(
        tmp_path,
        make_rule,
        write_files,
        '[tool.ruff.lint]\nselect = ["E"]\nignore = ["ALL"]\n',
        ["E501"],
    )
    assert f.verdict == "VERIFIED_IN_SCOPE"  # E(1) は ALL(0) より具体的
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["ALL"]\n', ["E501"])
    assert f.verdict == "VERIFIED_IN_SCOPE"


def test_ruff_extend_only_explicit_disable_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    base = 'extend = "../base.toml"\n[lint]\nextend-select = ["I"]\nignore = ["E501"]\n'
    # 子で明示的に select していても、親の ignore の引き継ぎを解析しないので UNKNOWN
    for required, verdict in (
        ("I001", "UNKNOWN"),
        ("B006", "UNKNOWN"),
        ("E501", "VIOLATED"),
        ("F401", "UNKNOWN"),
    ):
        params = {"tool": "ruff", "config_path": "ruff.toml", "required_rules": [required]}
        findings = _run(tmp_path, make_rule, write_files, {"ruff.toml": base}, params)
        assert findings[0].verdict == verdict, required


def test_ruff_redirected_names_match_both_ways(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["TCH"]\n', ["TC001"])
    assert f.verdict == "VERIFIED_IN_SCOPE"
    f = _ruff(tmp_path, make_rule, write_files, '[tool.ruff.lint]\nselect = ["TC"]\n', ["TCH001"])
    assert f.verdict == "VERIFIED_IN_SCOPE"
    f = _ruff(
        tmp_path,
        make_rule,
        write_files,
        '[tool.ruff.lint]\nselect = ["TCH"]\nignore = ["TC001"]\n',
        ["TCH001"],
    )
    assert f.verdict == "VIOLATED"


def test_no_required_rules_emits_no_config_finding(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {**RUFF_PARAMS, "required_rules": []}
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": CI_OK},
        params,
    )
    assert [f.requirement_id for f in findings] == ["LINT-001:.github/workflows/*.yml"]
    # 設定ファイルが無くても、必須規則が無ければ設定の Finding は出ない
    findings = _run(
        tmp_path / "x",
        make_rule,
        write_files,
        {".github/workflows/ci.yml": CI_OK},
        params,
    )
    assert [f.verdict for f in findings] == ["VERIFIED_IN_SCOPE"]


def test_eslint_extends_makes_unlisted_rules_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    cfg = json.dumps({"extends": "eslint:recommended", "rules": {"no-var": "error", "semi": "off"}})
    for required, verdict in (
        ("no-var", "VERIFIED_IN_SCOPE"),
        ("quotes", "UNKNOWN"),
        ("semi", "VIOLATED"),
    ):
        params = {"tool": "eslint", "config_path": ".eslintrc.json", "required_rules": [required]}
        findings = _run(tmp_path, make_rule, write_files, {".eslintrc.json": cfg}, params)
        assert findings[0].verdict == verdict, required


def test_two_ci_timeouts_stop_after_first(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    step = "      - run: " + "a" * 40 + "!\n"
    ci = "jobs:\n  a:\n    steps:\n" + step * 5
    params = {**RUFF_PARAMS, "required_rules": [], "ci_command_pattern": "(a|aa)+$"}
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"pyproject.toml": RUFF_OK, ".github/workflows/ci.yml": ci},
        params,
    )
    assert findings[0].verdict == "UNKNOWN"


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
    assert findings[0].verdict == "UNKNOWN"
    assert "タイムアウト" in findings[0].message
