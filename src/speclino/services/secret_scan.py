"""LLM に送る前の秘密情報・個人情報らしい値の検出と、モデルの引用の伏せ字（#114）。

LLM 支援の規約は対象リポジトリのコードの単位を外部のモデルに送る。秘密鍵・トークン・認証情報つきの
URL・メールアドレスなどを含む単位は送らずに UNKNOWN にし（`services/llm_policies.assess`）、モデルが
返した引用も同じ検出器で伏せてから保存・表示する。

検出は 3 段で、安全側（見逃しより誤検出を許す。誤検出はその単位が UNKNOWN になるだけ）:

1. 形による検出: 秘密鍵のヘッダー・既知のトークンの形・JWT・認証情報つきの URL・メールアドレス
2. 名前による検出: 代入先・キーワード引数・引数の既定値・dict のキー・属性名・比較の相手が
   秘密らしい名前（password / token / secret / api_key / auth / session など）なら、結びつく
   リテラルは形に関係なく秘密とみなす。環境変数・設定から読む式（リテラルを含まない）は送ってよい
3. 値による検出: 文字列リテラルの中の、長さ 20 以上・空白なし・英字と数字が混在する Base64 /
   16 進 / URL セーフの連なり（エントロピーの閾値は控えめ）。`os.environ` / `getenv` の変数名は
   外し、既定値は対象

**検出は完全ではない**。LLM 支援の規約は、送ってよいパスだけを `applies_to`（`params.paths`）に
指定する（README）。照合は線形時間に保つ: 正規表現は入れ子の量指定子を持たない形に限り、`regex`
パッケージの時間切れつきで照合する（時間切れは「検出した」とみなす）。AST の走査と値の連なりの
取り出しは 1 回の走査。
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
# Base64・16 進・URL セーフの文字（秘密らしい値の候補の連なり）
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")

# 名前による検出（#114）: 代入先・キーワード引数・dict のキー・属性名がこれを含めば（大文字小文字・
# 区切りを無視した部分一致）、その名前に結びつくリテラルは形に関係なく秘密とみなす
SENSITIVE_NAME_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "pwd",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "auth",
    "cookie",
    "session",
    "signingkey",
    "clientsecret",
    "bearer",
)
NAME_KIND = "秘密らしい名前に結びつく値"
VALUE_KIND = "秘密らしい文字列（長い英数字と記号）"
# 値による検出（#114）: 長さ 20 以上・空白なし・英字と数字が混在・エントロピーは控えめな閾値
VALUE_MIN_LENGTH = 20
VALUE_MIN_BITS = 3.0

# 文字の上での「名前 = 値」「名前: 値」（引用の伏せ字と、Python として読めない本文に使う）
_NAMED_VALUE = regex.compile(
    r"([A-Za-z_][A-Za-z0-9_\-]{0,63})[\"']?\s{0,3}[:=]\s{0,3}"
    r"(?:[rbuRBU]{0,2}([\"'])([^\"'\n]{1,256})\2|([^\s\"',;&()]{1,256}))"
)


@dataclass(frozen=True)
class SecretHit:
    kind: str
    start: int
    end: int


def sensitive_name(name: str) -> bool:
    folded = "".join(c for c in name.lower() if c.isalnum())
    return any(part in folded for part in SENSITIVE_NAME_PARTS)


def _example_email(text: str) -> bool:
    domain = text.rsplit("@", 1)[-1].lower()
    return domain.endswith(_EXAMPLE_TLDS) or any(
        domain == d or domain.endswith("." + d) for d in _EXAMPLE_DOMAINS
    )


def _timed_out(text: str) -> list[SecretHit]:
    # 照合しきれないものは送らない側に倒す（全体を検出扱い）
    return [SecretHit("照合の時間切れ", 0, len(text))]


def _pattern_hits(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for kind, pattern in _PATTERNS:
        try:
            for m in pattern.finditer(text, timeout=TIMEOUT_SECONDS):
                if kind == "メールアドレス" and _example_email(m.group(0)):
                    continue
                hits.append(SecretHit(kind, m.start(), m.end()))
        except TimeoutError:
            return _timed_out(text)
    return hits


def _named_value_hits(text: str) -> list[SecretHit]:
    """文字の上で、秘密らしい名前に `=` / `:` で結びつく値の範囲。"""
    hits: list[SecretHit] = []
    try:
        for m in _NAMED_VALUE.finditer(text, timeout=TIMEOUT_SECONDS):
            if not sensitive_name(m.group(1)):
                continue
            group = 3 if m.group(3) is not None else 4
            hits.append(SecretHit(NAME_KIND, m.start(group), m.end(group)))
    except TimeoutError:
        return _timed_out(text)
    return hits


def _entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def secret_like_value(token: str) -> bool:
    """値による検出の条件（空白を含まない 1 つの連なりについて）。"""
    return (
        len(token) >= VALUE_MIN_LENGTH
        and all(c in _TOKEN_CHARS for c in token)
        and any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
        and _entropy(token) >= VALUE_MIN_BITS
    )


def _value_runs(text: str, offset: int = 0) -> list[SecretHit]:
    """Base64・16 進・URL セーフの文字の連なりのうち、秘密らしい値の条件に合うもの。1 回の走査。"""
    hits: list[SecretHit] = []
    start: int | None = None
    for i, ch in enumerate(f"{text}\0"):
        if ch in _TOKEN_CHARS:
            if start is None:
                start = i
            continue
        if start is not None:
            if secret_like_value(text[start:i]):
                hits.append(SecretHit(VALUE_KIND, offset + start, offset + i))
            start = None
    return hits


# ---------------------------------------------------------------------------
# Python の本文（AST）: 名前による検出と、文字列リテラルの値による検出
# ---------------------------------------------------------------------------


def _is_environ(node: ast.expr) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ"
    )


def _env_key_ids(tree: ast.AST) -> set[int]:
    """環境変数の名前のリテラル（`os.environ["X"]`・`os.environ.get("X", ...)`・`getenv("X", ...)`
    の最初の引数）。読む値そのものは送ってよいので、名前は値による検出から外す。既定値は外さない。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            ids.add(id(node.slice))
        elif isinstance(node, ast.Call) and node.args:
            f = node.func
            getenv = (isinstance(f, ast.Attribute) and f.attr == "getenv") or (
                isinstance(f, ast.Name) and f.id == "getenv"
            )
            environ_get = isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value)
            if getenv or environ_get:
                ids.add(id(node.args[0]))
    return ids


def _literals(node: ast.AST, exempt: set[int]) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and id(n) not in exempt:
            if isinstance(n.value, str) and n.value:
                out.append(n.value)
            elif isinstance(n.value, bytes) and n.value:
                out.append(n.value.decode("latin-1"))
    return out


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
        return [str(target.slice.value)]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for t in target.elts for n in _target_names(t)]
    return []


def _named_values(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    """(名前, その名前に結びつく式)。代入・キーワード引数・引数の既定値・dict のキー・比較。"""
    pairs: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs += [(n, node.value) for t in node.targets for n in _target_names(t)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            pairs += [(n, node.value) for n in _target_names(node.target)]
        elif isinstance(node, ast.keyword) and node.arg is not None:
            pairs.append((node.arg, node.value))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    pairs.append((k.value, v))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            positional = [*a.posonlyargs, *a.args]
            pairs += (
                [
                    (arg.arg, d)
                    for arg, d in zip(positional[-len(a.defaults) :], a.defaults, strict=True)
                ]
                if a.defaults
                else []
            )
            pairs += [
                (arg.arg, d)
                for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True)
                if d is not None
            ]
        elif isinstance(node, ast.Compare):
            names = _target_names(node.left)
            pairs += [(n, c) for n in names for c in node.comparators]
    return pairs


def _python_hits(tree: ast.AST) -> list[SecretHit]:
    exempt = _env_key_ids(tree)
    hits: list[SecretHit] = []
    for name, value in _named_values(tree):
        if sensitive_name(name) and _literals(value, exempt):
            hits.append(SecretHit(NAME_KIND, 0, 0))
    for literal in _literals(tree, exempt):
        hits += [SecretHit(h.kind, 0, 0) for h in _value_runs(literal)]
    return hits


def find_secrets(text: str, *, python: bool = False) -> list[SecretHit]:
    """秘密情報・個人情報らしい値。

    `python=True` なら本文を AST で読み、名前による検出（秘密らしい名前に結びつくリテラル）と
    値による検出（文字列リテラルの中の秘密らしい連なり）を行う（位置は返さない）。読めなければ、また
    `python=False` なら、文字の上で同じ検出を行う（位置つき。引用の伏せ字に使う）。
    """
    hits = _pattern_hits(text)
    tree: ast.AST | None = None
    if python:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
    if tree is not None:
        return hits + _python_hits(tree)
    return hits + _named_value_hits(text) + _value_runs(text)


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
    "NAME_KIND",
    "SENSITIVE_NAME_PARTS",
    "VALUE_KIND",
    "MASK",
    "SECRET_REASON",
    "SecretHit",
    "find_secrets",
    "mask_secrets",
    "secret_kinds",
    "secret_like_value",
    "sensitive_name",
]
