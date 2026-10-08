"""提案の本文の途中経過（累積のスナップショット）の間引き。"""

from __future__ import annotations

import json

from speclino.services.draft_stream import DraftSnapshots


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _raw(text: str) -> str:
    return json.dumps({"explanation": "e", "proposal": {"body": text}}, ensure_ascii=False)


def test_snapshots_are_cumulative_and_throttled_by_time() -> None:
    clock = _Clock()
    d = DraftSnapshots("proposal", "body", interval=0.15, min_chars=1000, clock=clock)
    assert d.feed('{"explanation": "e", "proposal": {"body": "') is None
    assert d.feed("a") == "a"  # 最初は即時
    clock.now = 0.05
    assert d.feed("b") is None  # 間引く
    clock.now = 0.2
    assert d.feed("c") == "abc"  # 増分ではなく累積
    clock.now = 0.25
    assert d.feed("d") is None
    assert d.feed('"}}') == "abcd"  # 閉じたら間引きに関係なく最後の 1 件
    assert d.finish() is None


def test_closing_the_field_flushes_final_text_even_when_throttled() -> None:
    clock = _Clock()
    d = DraftSnapshots("proposal", "body", interval=10, min_chars=10_000, clock=clock)
    snaps = [s for c in _raw("hello world") if (s := d.feed(c)) is not None]
    assert snaps[-1] == "hello world"
    assert d.finish() is None  # 送り済み


def test_char_threshold_forces_a_snapshot() -> None:
    clock = _Clock()
    d = DraftSnapshots("proposal", "body", interval=10, min_chars=5, clock=clock)
    snaps = [s for c in _raw("x" * 12) if (s := d.feed(c)) is not None]
    assert snaps[0] == "x"
    assert any(len(s) == 6 for s in snaps)


def test_finish_flushes_truncated_text() -> None:
    clock = _Clock()
    d = DraftSnapshots("proposal", "body", interval=10, min_chars=10_000, clock=clock)
    d.feed('{"proposal": {"body": "ab')
    d.feed("cd")
    assert d.finish() == "abcd"
    assert d.finish() is None


def test_no_field_means_no_snapshot() -> None:
    d = DraftSnapshots("proposal", "body")
    assert d.feed('{"explanation": "e", "proposal": null}') is None
    assert d.finish() is None
