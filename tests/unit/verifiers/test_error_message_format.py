"""error_message.format の検証器テスト（S69）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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


def test_fstring_period_violation_from_constant_parts(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} failed.')\n",
        {"forbid_trailing_period": True},
    )
    assert findings[0].verdict == "VIOLATED"


def test_fstring_with_placeholder_pattern_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} を保存')\n",
        {"pattern": ".+"},
    )
    assert findings[0].verdict == "UNKNOWN"


def test_fstring_ja_without_japanese_constants_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    # 定数部分に日本語が無く、埋め込み式が日本語かもしれない -> 偽の VIOLATED / VERIFIED にしない
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{msg}')\napi_error(1, code='a', message=f'{a}: {b}')\n",
        {"language": "ja"},
    )
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
    # 定数部分に日本語があれば確定
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} を保存')\n",
        {"language": "ja"},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"
    # en は定数部分が ASCII のみなら、埋め込み式が非 ASCII かもしれず判定できない
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} saved')\n",
        {"language": "en"},
    )
    assert findings[0].verdict == "UNKNOWN"
    # en は定数部分の非 ASCII で違反が確定する
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'{x} を保存')\n",
        {"language": "en"},
    )
    assert findings[0].verdict == "VIOLATED"


def test_fstring_without_placeholder_is_like_a_literal(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    ok = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'保存しました')\n",
        {"pattern": ".+しました", "language": "ja"},
    )
    assert ok[0].verdict == "VERIFIED_IN_SCOPE"
    bad = _run(
        tmp_path,
        make_rule,
        write_files,
        "api_error(1, code='a', message=f'保存')\n",
        {"pattern": ".+しました"},
    )
    assert bad[0].verdict == "VIOLATED"


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


def test_syntax_error_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    assert _run(tmp_path, make_rule, write_files, "def (:\n")[0].verdict == "UNKNOWN"


def test_no_target_follows_on_missing_input(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for on_missing, expected in (
        ("violation", ["VIOLATED"]),
        ("unknown", ["UNKNOWN"]),
        ("skipped", []),
    ):
        rule = make_rule(
            id="ERRMSG-001",
            verifier_id="error_message.format",
            paths=["src/**/*.py"],
            params=PARAMS,
            on_missing_input=on_missing,
        )
        findings = ErrorMessageFormatVerifier().run(rule, DirectorySnapshot(root=tmp_path))
        assert [f.verdict for f in findings] == expected


def test_invalid_params_are_unknown_not_raised(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for params in ({"pattern": "(["}, {"language": "fr"}, {}, {"pattern": "a" * 300}):
        findings = _run(tmp_path, make_rule, write_files, None, params)
        assert [f.verdict for f in findings] == ["UNKNOWN"]
        assert findings[0].requirement_id == "ERRMSG-001"
        assert "パラメーターが不正" in findings[0].message


def test_catastrophic_pattern_times_out_to_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = f"api_error(1, code='a', message='{'a' * 40}!')\n"
    findings = _run(tmp_path, make_rule, write_files, src, {"pattern": "(a|aa)+$"})
    assert findings[0].verdict == "UNKNOWN"
    assert "タイムアウト" in (findings[0].actual or "")


def test_timeout_aborts_remaining_matches_in_file(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "".join(f"api_error(1, code='a', message='{'a' * 40}!')\n" for _ in range(10))
    start = time.monotonic()
    findings = _run(tmp_path, make_rule, write_files, src, {"pattern": "(a|aa)+$"})
    assert time.monotonic() - start < 0.5  # 10 回ぶんのタイムアウトを待たない
    assert findings[0].verdict == "UNKNOWN"
