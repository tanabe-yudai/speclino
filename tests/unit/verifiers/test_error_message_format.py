"""error_message.format の検証器テスト（S69）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from speclino.domain.finding import Finding
from speclino.verifiers.error_message_format import ErrorMessageFormatVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import MakeRule, WriteFiles

PARAMS: dict[str, Any] = {
    "pattern": r".+(しました|ません)",
    "require_code": True,
    "forbid_trailing_period": True,
    "language": "ja",
}


def _run(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    src: str | None,
    params: dict[str, Any] | None = None,
) -> list[Finding]:
    write_files(tmp_path, {} if src is None else {"src/a.py": src})
    rule = make_rule(
        id="ERRMSG-001",
        verifier_id="error_message.format",
        paths=["src/**/*.py"],
        params=PARAMS if params is None else params,
    )
    findings = ErrorMessageFormatVerifier().run(rule, DirectorySnapshot(root=tmp_path))
    for f in findings:
        Finding.model_validate(f.model_dump())
        assert f.verdict != "REVIEW"
    return findings


def test_conforming_messages_are_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = (
        "def f(x):\n"
        "    raise api_error(404, code='not_found', message='見つかりません')\n"
        "    raise HTTPException(status_code=400, code='bad', detail='入力を検証できません')\n"
        "    print('English.')\n"
    )
    findings = _run(tmp_path, make_rule, write_files, src)
    assert [f.requirement_id for f in findings] == ["ERRMSG-001:src/a.py"]
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_violations(tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles) -> None:
    src = (
        "api_error(404, code='a', message='not found')\n"  # 言語・パターン
        "api_error(404, message='見つかりません')\n"  # code なし
        "api_error(404, code='a', message='失敗しました。')\n"  # 句点
    )
    findings = _run(tmp_path, make_rule, write_files, src)
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "constraint_mismatch"
    actual = f.actual or ""
    assert "日本語が含まれていません" in actual
    assert "code= がありません" in actual
    assert "文末に句点" in actual
    assert [r.line_start for r in f.code_refs] == [1, 2, 3]


def test_en_language_and_default_call_names(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "raise HTTPException(status_code=1, detail='失敗')\n",
        {"language": "en"},
    )
    assert findings[0].verdict == "VIOLATED"
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "raise HTTPException(status_code=1, detail='failed')\nother(message='失敗')\n",
        {"language": "en"},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_fstring_checks_constant_parts_only(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} failed.')\n",
        {"language": "ja", "forbid_trailing_period": True, "require_code": True},
    )
    assert findings[0].verdict == "VIOLATED"
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} を保存')\n",
        {"pattern": ".+", "language": "ja"},
    )
    assert findings[0].verdict == "UNKNOWN"  # pattern は埋め込み式があると判定不能


def test_non_literal_message_is_unknown_and_violation_wins(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "api_error(1, code='a', message=MSG)\n"
    findings = _run(tmp_path, make_rule, write_files, src)
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
    findings = _run(
        tmp_path, make_rule, write_files, src + "api_error(2, code='a', message='bad')\n"
    )
    assert findings[0].verdict == "VIOLATED"
    assert "判定不能" in (findings[0].actual or "")


def test_positional_only_message_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(404, 'x', '見つかりません')\n",
        {"language": "ja"},
    )
    assert findings[0].verdict == "UNKNOWN"


def test_syntax_error_is_unknown_and_no_input_has_no_findings(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    assert _run(tmp_path, make_rule, write_files, "def (:\n")[0].verdict == "UNKNOWN"
    assert _run(tmp_path / "empty", make_rule, write_files, None) == []


def test_invalid_params_raise_value_error(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, None, {"pattern": "(["})
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, None, {"language": "fr"})


def test_catastrophic_pattern_times_out_to_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = f"api_error(1, code='a', message='{'a' * 40}!')\n"
    findings = _run(tmp_path, make_rule, write_files, src, {"pattern": "(a|aa)+$"})
    assert findings[0].verdict == "UNKNOWN"
    assert "タイムアウト" in (findings[0].actual or "")
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, src, {"pattern": "a" * 300})
