"""LLM に送る前の秘密情報・個人情報らしい値の検出と、モデルの引用の伏せ字（#114）。

LLM 支援の規約は対象リポジトリのコードの単位を外部のモデルに送る。秘密鍵・トークン・認証情報つきの
URL・メールアドレスなどを含む単位は送らずに UNKNOWN にし（`services/llm_policies.assess`）、モデルが
返した引用も同じ検出器で伏せてから保存・表示する。

検出は安全側（見逃しより誤検出を許す）。照合は線形時間に保つ: 正規表現は入れ子の量指定子を持たない
形に限り、`regex` パッケージの時間切れつきで照合する（時間切れは「検出した」とみなす。送らない側に
倒す）。高いエントロピーの文字列は正規表現を使わず 1 回の走査で取り出す。
"""

from __future__ import annotations

import ast
import math
from collections import Counter
from dataclasses import dataclass

import regex

SECRET_REASON = "秘密情報らしい値を含むため LLM に送らない"
MASK = "[秘密情報らしい値を伏せた]"
TIMEOUT_SECONDS = 0.5
# 高いエントロピーの文字列とみなす長さと 1 文字あたりのビット数
ENTROPY_MIN_LENGTH = 32
ENTROPY_MIN_BITS = 3.5

_PATTERNS: tuple[tuple[str, regex.Pattern[str]], ...] = tuple(
    (kind, regex.compile(p))
    for kind, p in (
        ("秘密鍵", r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"),
        ("AWS のアクセスキー", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b"),
        ("GCP の API キー", r"\bAIza[0-9A-Za-z_\-]{35}"),
        ("GCP のサービスアカウント鍵", r"\"private_key_id\"\s{0,5}:"),
        ("GitHub のトークン", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}"),
        ("GitHub のトークン", r"\bgithub_pat_[A-Za-z0-9_]{22,255}"),
        ("Slack のトークン", r"\bxox[abposr]-[A-Za-z0-9\-]{10,255}"),
        ("Slack の Webhook", r"hooks\.slack\.com/services/[A-Za-z0-9/]{8,255}"),
        ("API キー（sk-）", r"\bsk-[A-Za-z0-9_\-]{20,255}"),
        ("JWT", r"\beyJ[A-Za-z0-9_\-]{8,4096}\.eyJ[A-Za-z0-9_\-]{8,4096}\.[A-Za-z0-9_\-]{8,4096}"),
        (
            "認証情報つきの URL",
            r"[A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s/:@\"']{1,256}:[^\s/@\"']{1,256}@",
        ),
        ("メールアドレス", r"[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,63}"),
    )
)

# 例示用に予約されたドメイン（RFC 2606・6761）。個人情報ではないので検出しない
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
_EXAMPLE_TLDS = (".example", ".invalid", ".test", ".localhost")
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-.")


@dataclass(frozen=True)
class SecretHit:
    kind: str
    start: int
    end: int


def _example_email(text: str) -> bool:
    domain = text.rsplit("@", 1)[-1].lower()
    return domain.endswith(_EXAMPLE_TLDS) or any(
        domain == d or domain.endswith("." + d) for d in _EXAMPLE_DOMAINS
    )


def _pattern_hits(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for kind, pattern in _PATTERNS:
        try:
            for m in pattern.finditer(text, timeout=TIMEOUT_SECONDS):
                if kind == "メールアドレス" and _example_email(m.group(0)):
                    continue
                hits.append(SecretHit(kind, m.start(), m.end()))
        except TimeoutError:
            # 照合しきれないものは送らない側に倒す（全体を検出扱い）
            return [SecretHit("照合の時間切れ", 0, len(text))]
    return hits


def _entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _high_entropy_runs(text: str, offset: int = 0) -> list[SecretHit]:
    """英数字と記号の連なり（長さ 32 以上・英字と数字を含む・エントロピーが高い）。1 回の走査。"""
    hits: list[SecretHit] = []
    start: int | None = None
    for i, ch in enumerate(f"{text}\0"):
        if ch in _TOKEN_CHARS:
            if start is None:
                start = i
            continue
        if start is not None:
            token = text[start:i]
            if (
                len(token) >= ENTROPY_MIN_LENGTH
                and any(c.isdigit() for c in token)
                and any(c.isalpha() for c in token)
                and _entropy(token) >= ENTROPY_MIN_BITS
            ):
                hits.append(SecretHit("高いエントロピーの文字列", offset + start, offset + i))
            start = None
    return hits


def _string_literals(text: str) -> list[str] | None:
    """Python の本文の文字列リテラルの値。読めなければ None（本文全体を走査する）。"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    return [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def find_secrets(text: str, *, python: bool = False) -> list[SecretHit]:
    """秘密情報・個人情報らしい値。`python=True` なら高エントロピーの検出を文字列リテラルに限る。"""
    hits = _pattern_hits(text)
    literals = _string_literals(text) if python else None
    if literals is None:
        hits += _high_entropy_runs(text)
    else:
        for value in literals:
            # 位置は本文と対応しないので、検出の有無だけを返す
            hits += [SecretHit(h.kind, 0, 0) for h in _high_entropy_runs(value)]
    return hits


def secret_kinds(text: str, *, python: bool = False) -> list[str]:
    """検出した種類（重複なし・決まった順）。値そのものは返さない。"""
    return sorted({h.kind for h in find_secrets(text, python=python)})


def mask_secrets(text: str) -> str:
    """検出した範囲を伏せ字にする（モデルの引用を保存・表示する前に使う）。"""
    hits = sorted(
        (h for h in find_secrets(text) if h.end > h.start), key=lambda h: (h.start, -h.end)
    )
    out: list[str] = []
    pos = 0
    for h in hits:
        if h.end <= pos:
            continue
        out.append(text[pos : max(pos, h.start)])
        out.append(MASK)
        pos = h.end
    out.append(text[pos:])
    return "".join(out)


__all__ = [
    "MASK",
    "SECRET_REASON",
    "SecretHit",
    "find_secrets",
    "mask_secrets",
    "secret_kinds",
]
