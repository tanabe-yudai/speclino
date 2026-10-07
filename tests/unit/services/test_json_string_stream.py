"""途中までの JSON から説明の文字列を少しずつ取り出す（S74）。"""

from __future__ import annotations

import json

import pytest

from speclino.services.json_string_stream import TopLevelStringStream


def _run(raw: str, size: int) -> str:
    stream = TopLevelStringStream("explanation")
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
    s = TopLevelStringStream("explanation")
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
    s = TopLevelStringStream("explanation")
    assert s.feed('{"explanation": 12}') == ""
    assert s.done
    s2 = TopLevelStringStream("explanation")
    assert s2.feed('{"explanation": "a\\qb"}') == "a"
    assert s2.done


def test_truncated_input_returns_what_is_decoded() -> None:
    assert _run('{"explanation": "途中で切れ', 4) == "途中で切れ"
    # \u の途中で切れたら、その文字は出さない
    assert _run('{"explanation": "a\\u30', 3) == "a"
