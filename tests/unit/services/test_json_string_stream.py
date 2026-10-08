"""途中までの JSON から説明の文字列を少しずつ取り出す（S74）。"""

from __future__ import annotations

import json

import pytest

from speclino.services.json_string_stream import JsonStringStream


def _run(raw: str, size: int) -> str:
    stream = JsonStringStream("explanation")
    return "".join(stream.feed(raw[i : i + size]) for i in range(0, len(raw), size))


@pytest.mark.parametrize("size", [1, 2, 3, 5, 1000])
def test_extracts_explanation_regardless_of_chunking(size: int) -> None:
    text = 'こんにちは "引用" \\ 改行\n タブ\t 😀 / end'
    raw = json.dumps({"explanation": text, "proposal": {"body": "x"}}, ensure_ascii=False)
    assert _run(raw, size) == text


@pytest.mark.parametrize("size", [1, 2, 3, 7])
def test_decodes_unicode_escapes_and_surrogate_pairs(size: int) -> None:
    text = "日本語 😀 é \u0001"
    raw = json.dumps({"explanation": text, "proposal": None})  # ensure_ascii=True → \uXXXX
    assert "\\ud83d\\ude00" in raw
    assert _run(raw, size) == text


def test_emits_nothing_before_key_and_incrementally_after() -> None:
    s = JsonStringStream("explanation")
    assert s.feed('{"expl') == ""
    assert s.feed('anation": "ab') == "ab"
    assert s.feed("c") == "c"
    assert not s.done
    assert s.feed('d", "proposal": {"explanation": "no"}}') == "d"
    assert s.done


def test_ignores_key_inside_nested_or_string_value() -> None:
    raw = (
        '{"proposal": {"explanation": "nested"}, "note": "\\"explanation\\": \\"x\\"", '
        '"explanation": "real"}'
    )
    assert _run(raw, 4) == "real"


def test_explanation_after_other_keys() -> None:
    assert _run('{"a": [1, {"b": "c"}], "explanation": "ok"}', 3) == "ok"


def test_lone_surrogates_become_replacement_char() -> None:
    assert _run('{"explanation": "a\\ud83dz\\ude00"}', 2) == "a�z�"


def test_non_string_value_or_bad_escape_stops() -> None:
    s = JsonStringStream("explanation")
    assert s.feed('{"explanation": 12}') == ""
    assert s.done
    s2 = JsonStringStream("explanation")
    assert s2.feed('{"explanation": "a\\qb"}') == "a"
    assert s2.done


def test_truncated_input_returns_what_is_decoded() -> None:
    assert _run('{"explanation": "途中で切れ', 4) == "途中で切れ"
    # \u の途中で切れたら、その文字は出さない
    assert _run('{"explanation": "a\\u30', 3) == "a"


# ---- 入れ子の経路（提案の本文）
def _run_path(raw: str, size: int, *path: str) -> str:
    stream = JsonStringStream(*path)
    return "".join(stream.feed(raw[i : i + size]) for i in range(0, len(raw), size))


_TEXT = '# 見出し\n"引用" \\ \t 😀 / end'


@pytest.mark.parametrize("ascii_", [True, False])
@pytest.mark.parametrize("size", [1, 2, 3, 5, 1000])
def test_nested_path_regardless_of_chunking(size: int, ascii_: bool) -> None:
    raw = json.dumps(
        {"explanation": "説明", "proposal": {"files": [{"body": "x"}], "markdown": _TEXT}},
        ensure_ascii=ascii_,
    )
    assert _run_path(raw, size, "proposal", "markdown") == _TEXT


def test_nested_path_split_at_every_position() -> None:
    raw = json.dumps({"explanation": "e", "proposal": {"markdown": _TEXT}})
    for i in range(len(raw) + 1):
        s = JsonStringStream("proposal", "markdown")
        assert s.feed(raw[:i]) + s.feed(raw[i:]) == _TEXT


def test_nested_ignores_same_key_elsewhere() -> None:
    raw = '{"markdown": "top", "proposal": {"x": {"markdown": "deep"}, "markdown": "right"}}'
    assert _run_path(raw, 3, "proposal", "markdown") == "right"


def test_nested_duplicate_keys_stream_only_first() -> None:
    raw = '{"proposal": {"body": "one", "body": "two"}, "proposal": {"body": "three"}}'
    assert _run_path(raw, 2, "proposal", "body") == "one"


def test_nested_missing_field_or_null_proposal() -> None:
    raw = '{"explanation": "e", "proposal": {"other": "x"}, "markdown": "m"}'
    assert _run_path(raw, 4, "proposal", "markdown") == ""
    s = JsonStringStream("proposal", "markdown")
    assert s.feed('{"proposal": null, "markdown": "m"}') == "" and s.done
    s2 = JsonStringStream("proposal", "markdown")
    assert s2.feed('{"proposal": {}, "proposal2": {"markdown": "m"}}') == "" and s2.done


def test_nested_truncated_returns_decoded_part() -> None:
    raw = '{"proposal": {"markdown": "途中で切れ'
    assert _run_path(raw, 5, "proposal", "markdown") == "途中で切れ"


def test_empty_path_is_rejected() -> None:
    with pytest.raises(ValueError):
        JsonStringStream()
