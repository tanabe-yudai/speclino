"""提案の本文を、生成の途中経過（累積のスナップショット）として間引いて返す。

増分ではなく「ここまでの全文」を送るので、取りこぼしても次の 1 件で追いつく。
途中経過は表示用で、確定値は常に最後の `done` の提案から取る。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from speclino.services.json_string_stream import JsonStringStream

DRAFT_INTERVAL_SECONDS = 0.15
DRAFT_MIN_CHARS = 400


class DraftSnapshots:
    """`path` の文字列を feed するたびに、送るべきときだけ累積の全文を返す。"""

    def __init__(
        self,
        *path: str,
        interval: float = DRAFT_INTERVAL_SECONDS,
        min_chars: int = DRAFT_MIN_CHARS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = JsonStringStream(*path)
        self._interval = interval
        self._min_chars = min_chars
        self._clock = clock
        self._text = ""
        self._sent_len = 0
        self._sent_at: float | None = None

    def feed(self, chunk: str) -> str | None:
        piece = self._stream.feed(chunk)
        if not piece and not (self._stream.done and self._sent_len != len(self._text)):
            return None
        self._text += piece
        if not self._text:
            return None
        now = self._clock()
        due = (
            self._sent_at is None
            or now - self._sent_at >= self._interval
            or len(self._text) - self._sent_len >= self._min_chars
        )
        # 本文が閉じたら、間引きに関係なく最後の 1 件を送る
        if due or self._stream.done:
            return self._take(now)
        return None

    def finish(self) -> str | None:
        """入力が尽きたとき、未送信の分があれば全文を返す（途中で切れても見せる）。"""
        if len(self._text) == self._sent_len:
            return None
        return self._take(self._clock())

    def _take(self, now: float) -> str:
        self._sent_len = len(self._text)
        self._sent_at = now
        return self._text


__all__ = ["DRAFT_INTERVAL_SECONDS", "DRAFT_MIN_CHARS", "DraftSnapshots"]
