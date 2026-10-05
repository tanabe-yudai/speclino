"""規約パラメーターの正規表現を安全に照合する（ReDoS 対策）。

パターンは利用者入力なので、``regex`` パッケージの ``timeout=`` 付きで照合し、パターン長・
対象文字列長にも上限を置く（パターンの検証は ``domain/policy_params.py``）。タイムアウトまたは
対象が長すぎる場合は ``RegexTimeout``。呼び出し側はその対象を UNKNOWN にする（違反にしない）。
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


def fullmatch(pattern: regex.Pattern[str], subject: str) -> bool:
    _guard(subject)
    try:
        return pattern.fullmatch(subject, timeout=TIMEOUT_SECONDS) is not None
    except TimeoutError as e:
        raise RegexTimeout("正規表現の照合がタイムアウトしました") from e


def search(pattern: regex.Pattern[str], subject: str) -> bool:
    _guard(subject)
    try:
        return pattern.search(subject, timeout=TIMEOUT_SECONDS) is not None
    except TimeoutError as e:
        raise RegexTimeout("正規表現の照合がタイムアウトしました") from e
