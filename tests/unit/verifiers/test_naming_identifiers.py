"""naming.identifiers の検証器テスト（S69）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from speclino.domain.finding import Finding
from speclino.verifiers.naming_identifiers import NamingIdentifiersVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import MakeRule, WriteFiles

PARAMS: dict[str, Any] = {
    "function": "[a-z][a-z0-9_]*",
    "class": "[A-Z][A-Za-z0-9]*",
    "constant": "[A-Z][A-Z0-9_]*",
    "module": "[a-z][a-z0-9_]*",
    "forbidden_words": ["tmp", "util"],
}


def _run(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    files: dict[str, str],
    params: dict[str, Any] | None = None,
) -> list[Finding]:
    write_files(tmp_path, files)
    rule = make_rule(
        id="NAME-001",
        verifier_id="naming.identifiers",
        paths=["src/**/*.py"],
        params=PARAMS if params is None else params,
    )
    findings = NamingIdentifiersVerifier().run(rule, DirectorySnapshot(root=tmp_path))
    for f in findings:
        Finding.model_validate(f.model_dump())
    return findings


def test_conforming_file_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = (
        "MAX_RETRY = 3\n_PRIVATE_LIMIT: int = 1\n__all__ = []\n\n"
        "class UserAccount:\n"
        "    def __init__(self): ...\n"
        "    def _load_user(self): ...\n\n"
        "async def fetch_database(): ...\n"
    )
    findings = _run(tmp_path, make_rule, write_files, {"src/user_service.py": src})
    assert [f.requirement_id for f in findings] == ["NAME-001:src/user_service.py"]
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_violations_are_reported_with_lines(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = (
        "maxRetry = 3\nBadConst = 1\n"  # 小文字混じりは定数ではない（検査対象外）
        "TIMEOUT_Value = 1\n"  # 定数ではない
        "def FetchUser(): ...\n"
        "class user_account: ...\n"
        "def tmp_helper(): ...\n"
        "def parseUtil(): ...\n"
    )
    findings = _run(tmp_path, make_rule, write_files, {"src/UserService.py": src})
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "constraint_mismatch"
    actual = f.actual or ""
    assert "module 'UserService'" in actual
    assert "function 'FetchUser'" in actual
    assert "class 'user_account'" in actual
    assert "禁止語 tmp" in actual
    assert "禁止語 util" in actual  # parseUtil -> parse, util
    assert "maxRetry" not in actual
    assert [r.line_start for r in f.code_refs] == [4, 5, 6, 7]


def test_forbidden_word_is_word_based_not_substring(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/a.py": "def utility_belt(): ...\ndef tmpl(): ...\n"},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_constant_regex_applies_to_all_caps_only(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {"constant": "[A-Z]+_[A-Z]+"}
    findings = _run(tmp_path, make_rule, write_files, {"src/a.py": "MAXRETRY = 1\n"}, params)
    assert findings[0].verdict == "VIOLATED"


def test_syntax_error_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(tmp_path, make_rule, write_files, {"src/a.py": "def (:\n"})
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_no_matching_file_yields_no_findings(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    assert _run(tmp_path, make_rule, write_files, {"docs/readme.md": "x"}) == []


def test_invalid_params_raise_value_error(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {"function": "[a-"})
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {"funtion": "x"})


def test_catastrophic_pattern_times_out_to_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = f"def {'a' * 40}_(): ...\n"
    findings = _run(tmp_path, make_rule, write_files, {"src/m.py": src}, {"function": "(a|aa)+$"})
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
    assert "タイムアウト" in findings[0].message


def test_overlong_identifier_is_unknown_and_long_pattern_rejected(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = f"def {'a' * 1001}(): ...\n"
    findings = _run(tmp_path, make_rule, write_files, {"src/m.py": src}, {"function": "a+"})
    assert findings[0].verdict == "UNKNOWN"
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {"function": "a" * 257})
