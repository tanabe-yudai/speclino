"""規約パラメーターの正規表現を安全に照合する（ReDoS 対策）。

パターンは利用者入力なので、``regex`` パッケージの ``timeout=`` 付きで照合し、パターン長・
対象文字列長にも上限を置く（パターンの検証は ``domain/policy_params.py``）。タイムアウトまたは
対象が長すぎる場合は ``RegexTimeout``。``RegexMatcher`` は最初のタイムアウト以降の照合を打ち切る。
呼び出し側はその対象を UNKNOWN にする（違反にしない）。
"""

from __future__ import annotations

import regex

MAX_SUBJECT_LENGTH = 1000
TIMEOUT_SECONDS = 0.05


class RegexTimeout(Exception):
    """照合がタイムアウトした、または対象が長すぎて照合しなかった。"""


def compile_param(pattern: str) -> regex.Pattern[str]:
    """検証済みのパターンをコンパイルする（不正なら ``ValueError``）。"""
    try:
        return regex.compile(pattern)
    except regex.error as e:
        raise ValueError(f"正規表現として不正です: {e}") from e


def _guard(subject: str) -> None:
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise RegexTimeout(f"正規表現の照合対象が長すぎます（{len(subject)} 文字）")


class RegexMatcher:
    """ファイル（または 1 回の検査）単位の照合器。

    最初のタイムアウト以降は、残りの照合を行わず ``RegexTimeout`` にする（累計時間の上限）。
    """

    def __init__(self) -> None:
        self._timed_out = False

    def _run(self, op: str, pattern: regex.Pattern[str], subject: str) -> bool:
        if self._timed_out:
            raise RegexTimeout(
                "先に正規表現の照合がタイムアウトしたため、残りの照合を打ち切りました"
            )
        _guard(subject)
        try:
            if op == "fullmatch":
                return pattern.fullmatch(subject, timeout=TIMEOUT_SECONDS) is not None
            return pattern.search(subject, timeout=TIMEOUT_SECONDS) is not None
        except TimeoutError as e:
            self._timed_out = True
            raise RegexTimeout("正規表現の照合がタイムアウトしました") from e

    def fullmatch(self, pattern: regex.Pattern[str], subject: str) -> bool:
        return self._run("fullmatch", pattern, subject)

    def search(self, pattern: regex.Pattern[str], subject: str) -> bool:
        return self._run("search", pattern, subject)
