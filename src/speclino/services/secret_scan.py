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
import string
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


_EXAMPLE_HOST = regex.compile(r"[a-z0-9\-]{1,63}\.(?:example|invalid|test|localhost)")
_EXAMPLE_LOCAL = regex.compile(r"[a-z][a-z0-9.\-]{0,30}")


def _example_email(text: str) -> bool:
    """例示用のアドレスに完全一致するか。ドメインは `example.com` などそのものか `<1 語>.test`、
    ローカル部は短い小文字の語だけ（秘密をローカル部に埋めた形は除外しない。#114 fix round 2）。"""
    local, _, domain = text.rpartition("@")
    domain = domain.lower()
    example_domain = domain in _EXAMPLE_DOMAINS or _EXAMPLE_HOST.fullmatch(domain) is not None
    return (
        example_domain
        and _EXAMPLE_LOCAL.fullmatch(local) is not None
        and not secret_like_value(local)
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


_WORD = regex.compile(r"[a-z]{1,30}[0-9]{0,2}|[0-9]{1,4}")


def _wordish(token: str) -> bool:
    """Python の識別子の形（`retry_policy_v2`・`UTF8_INPUT_LIMIT`）に完全一致するか。

    小文字だけ・大文字だけのどちらかで、`_` で区切った 2〜16 語。各語は英字（末尾に数字 2 桁まで）か
    4 桁までの数字で、半分以上の語が 3 文字以上の英字。大文字小文字の混じり・短い語ばかりの並びは
    識別子とみなさない（#114 fix round 2: 秘密を識別子の形に似せて除外をすり抜けさせない）。
    本文の中の識別子にだけ使い、文字列リテラルの値には使わない。
    """
    lowered = token.lower()
    if token not in (lowered, token.upper()):
        return False
    words = lowered.split("_")
    if not 2 <= len(words) <= 16 or any(_WORD.fullmatch(w) is None for w in words):
        return False
    long_words = sum(1 for w in words if len(w.rstrip("0123456789")) >= 3)
    return long_words * 2 >= len(words)


def secret_like_value(token: str) -> bool:
    """値による検出の条件（空白を含まない 1 つの連なりについて）。"""
    return (
        len(token) >= VALUE_MIN_LENGTH
        and all(c in _TOKEN_CHARS for c in token)
        and any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
        and _entropy(token) >= VALUE_MIN_BITS
    )


def _value_runs(text: str, offset: int = 0, *, allow_identifiers: bool = True) -> list[SecretHit]:
    """Base64・16 進・URL セーフの文字の連なりのうち、秘密らしい値の条件に合うもの。1 回の走査。"""
    hits: list[SecretHit] = []
    start: int | None = None
    for i, ch in enumerate(f"{text}\0"):
        if ch in _TOKEN_CHARS:
            if start is None:
                start = i
            continue
        if start is not None:
            token = text[start:i]
            if secret_like_value(token) and not (allow_identifiers and _wordish(token)):
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
            if _env_name(node.slice):
                ids.add(id(node.slice))
        elif isinstance(node, ast.Call) and node.args:
            f = node.func
            getenv = (isinstance(f, ast.Attribute) and f.attr == "getenv") or (
                isinstance(f, ast.Name) and f.id == "getenv"
            )
            environ_get = isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value)
            if (getenv or environ_get) and _env_name(node.args[0]):
                ids.add(id(node.args[0]))
    return ids


_ENV_NAME = regex.compile(r"[A-Z_][A-Z0-9_]{0,127}")


def _env_name(n: ast.AST) -> bool:
    """環境変数の名前の形（大文字・数字・`_`）に完全一致する定数だけを除外する。"""
    return (
        isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and _ENV_NAME.fullmatch(n.value) is not None
    )


def _constant_text(n: ast.AST) -> str | None:
    if isinstance(n, ast.Constant):
        if isinstance(n.value, str):
            return n.value
        if isinstance(n.value, bytes):
            return n.value.decode("latin-1")  # bytes は latin-1 で文字列にして検査する
    return None


# 組み立ての上限（#114 fix round 2）。組み立てる「前に」出力の大きさを見積もり、超えるなら
# 組み立てずにその単位を UNKNOWN にする（`%99999999s`・`"ab" * 10**9` で資源を使い切らせない）
MAX_ASSEMBLED = 64 * 1024
MAX_FORMAT_WIDTH = 1024
UNANALYZABLE_KIND = "解析できない書式"
_PERCENT_SPEC = regex.compile(
    r"%(?:\([^)]{0,64}\))?[#0\- +]{0,8}(\*|\d{1,32})?(?:\.(\*|\d{1,32}))?[hlL]?(.)",
    flags=regex.DOTALL,
)
_SPEC_NUMBER = regex.compile(r"\d{1,32}")
_COMPOSITES = (ast.BinOp, ast.JoinedStr, ast.Call)


class _Unanalyzable(Exception):
    """組み立てると上限を超える・書式が解析できない。その単位は送らない。"""


class _Assembler:
    """定数の式を組み立てる。出力の合計を `MAX_ASSEMBLED` で抑え、超える前に `_Unanalyzable`。"""

    def __init__(self) -> None:
        self.budget = MAX_ASSEMBLED

    def _charge(self, size: int) -> None:
        if size > self.budget:
            raise _Unanalyzable(f"組み立てると {size} 文字を超える")
        self.budget -= size

    def assemble(self, n: ast.AST) -> str | None:
        """定数だけでできた式なら組み立てた値、そうでなければ None。"""
        text = _constant_text(n)
        if text is not None:
            return text
        if isinstance(n, ast.JoinedStr):
            return self._fstring(n)
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, ast.Add):
                return self._concat(n)
            if isinstance(n.op, ast.Mult):
                return self._repeat(n)
            if isinstance(n.op, ast.Mod):
                return self._percent(n)
            return None
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and not n.keywords:
            if n.func.attr == "format":
                return self._format(n)
            if n.func.attr == "join":
                return self._join(n)
        return None

    def _all(self, nodes: list[ast.AST]) -> list[str] | None:
        """すべてを組み立てる（1 つが定数でなくても残りの上限の検査は続ける）。"""
        texts = [self.assemble(x) for x in nodes]
        if any(t is None for t in texts):
            return None
        return [t for t in texts if t is not None]

    def _fstring(self, n: ast.JoinedStr) -> str:
        parts: list[str] = []
        for v in n.values:
            text = _constant_text(v)
            if text is not None:
                parts.append(text)
            elif isinstance(v, ast.FormattedValue) and isinstance(v.format_spec, ast.JoinedStr):
                spec_parts = [_constant_text(x) for x in v.format_spec.values]
                if any(t is None for t in spec_parts):
                    raise _Unanalyzable("入れ子の書式指定")
                _check_spec("".join(t for t in spec_parts if t is not None))
        out = "".join(parts)
        self._charge(len(out))
        return out

    def _concat(self, n: ast.BinOp) -> str | None:
        # `+` の連なりは明示のスタックで平らにする（深い連結で再帰しない）
        operands: list[ast.AST] = []
        stack: list[ast.AST] = [n]
        while stack:
            cur = stack.pop()
            if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                stack += [cur.right, cur.left]
            else:
                operands.append(cur)
        texts = self._all(operands)
        if texts is None:
            return None
        self._charge(sum(map(len, texts)))
        return "".join(texts)

    def _repeat(self, n: ast.BinOp) -> str | None:
        for text_node, count_node in ((n.left, n.right), (n.right, n.left)):
            if (
                isinstance(count_node, ast.Constant)
                and isinstance(count_node.value, int)
                and not isinstance(count_node.value, bool)
            ):
                text = self.assemble(text_node)
                if text is None:
                    continue
                count = max(count_node.value, 0)
                self._charge(len(text) * count)  # 掛ける前に見積もる
                return text * count
        for text_node, count_node in ((n.left, n.right), (n.right, n.left)):
            if _constant_int_expr(count_node) and self.assemble(text_node) is not None:
                # `"ab" * 10**9` の回数は計算しない（計算すると見積もりの前に大きくなる）
                raise _Unanalyzable("反復の回数を定数の式から決められない")
        return None

    def _percent(self, n: ast.BinOp) -> str | None:
        fmt = self.assemble(n.left)
        if fmt is None:
            return None
        right = n.right.elts if isinstance(n.right, ast.Tuple) else [n.right]
        # 書式の幅・精度は引数より先に見る（引数が定数でなくても、大きすぎる書式は解析しない）
        _check_percent(fmt)
        args = self._all(list(right))
        if args is None:
            return None
        out: list[str] = []
        pos = 0
        consumed = 0
        try:
            for m in _PERCENT_SPEC.finditer(fmt, timeout=TIMEOUT_SECONDS):
                out.append(fmt[pos : m.start()])
                pos = m.end()
                width, precision, conv = m.group(1), m.group(2), m.group(3)
                for number in (width, precision):
                    if number == "*" or (number is not None and int(number) > MAX_FORMAT_WIDTH):
                        raise _Unanalyzable("書式の幅・精度が大きすぎる")
                if conv == "%":
                    out.append("%")
                    continue
                if consumed < len(args):
                    out.append(args[consumed])  # 幅は詰め物なので値だけをつなぐ（安全側）
                    consumed += 1
        except TimeoutError as e:
            raise _Unanalyzable("書式の照合の時間切れ") from e
        out.append(fmt[pos:])
        self._charge(sum(map(len, out)))
        return "".join(out)

    def _format(self, n: ast.Call) -> str | None:
        assert isinstance(n.func, ast.Attribute)
        fmt = self.assemble(n.func.value)
        if fmt is None:
            return None
        try:
            for _literal, field, spec, _conv in string.Formatter().parse(fmt):
                if field is not None:
                    _check_spec(spec or "")
        except ValueError as e:
            raise _Unanalyzable("書式を解析できない") from e
        args = self._all(list(n.args))
        if args is None:
            return None
        out: list[str] = []
        auto = 0
        try:
            for literal, field, spec, _conv in string.Formatter().parse(fmt):
                out.append(literal)
                if field is None:
                    continue
                _check_spec(spec or "")
                if field == "":
                    index, auto = auto, auto + 1
                elif field.isdigit():
                    index = int(field)
                else:
                    continue
                if index < len(args):
                    out.append(args[index])
        except ValueError as e:
            raise _Unanalyzable("書式を解析できない") from e
        self._charge(sum(map(len, out)))
        return "".join(out)

    def _join(self, n: ast.Call) -> str | None:
        assert isinstance(n.func, ast.Attribute)
        sep = self.assemble(n.func.value)
        if sep is None or len(n.args) != 1 or not isinstance(n.args[0], (ast.List, ast.Tuple)):
            return None
        items = self._all(list(n.args[0].elts))
        if items is None:
            return None
        self._charge(sum(map(len, items)) + len(sep) * max(len(items) - 1, 0))
        return sep.join(items)


def _check_percent(fmt: str) -> None:
    try:
        for m in _PERCENT_SPEC.finditer(fmt, timeout=TIMEOUT_SECONDS):
            for number in (m.group(1), m.group(2)):
                if number == "*" or (number is not None and int(number) > MAX_FORMAT_WIDTH):
                    raise _Unanalyzable("書式の幅・精度が大きすぎる")
    except TimeoutError as e:
        raise _Unanalyzable("書式の照合の時間切れ") from e


def _constant_int_expr(n: ast.AST) -> bool:
    """整数の定数だけでできた式（`10**9`・`1 << 30`・`-5`）。"""
    stack = [n]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.Constant):
            if not isinstance(cur.value, int) or isinstance(cur.value, bool):
                return False
        elif isinstance(cur, ast.BinOp):
            stack += [cur.left, cur.right]
        elif isinstance(cur, ast.UnaryOp):
            stack.append(cur.operand)
        else:
            return False
    return True


def _assembling(n: ast.AST) -> bool:
    """組み立ての中で子を読む式（子は一番外側の式の組み立ての中で扱う）。"""
    if isinstance(n, ast.BinOp):
        return isinstance(n.op, (ast.Add, ast.Mult, ast.Mod))
    if isinstance(n, ast.JoinedStr):
        return True
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("format", "join")
    )


def _check_spec(spec: str) -> None:
    """書式指定（`>99999999`・入れ子の `{}`）が大きすぎる・解析できないなら `_Unanalyzable`。"""
    if "{" in spec or "}" in spec:
        raise _Unanalyzable("入れ子の書式指定")
    for m in _SPEC_NUMBER.finditer(spec):
        if int(m.group(0)) > MAX_FORMAT_WIDTH:
            raise _Unanalyzable("書式の幅・精度が大きすぎる")


def _literals(node: ast.AST, exempt: set[int]) -> list[str]:
    """部分木のすべての文字列・bytes の定数の値（エスケープを解いた後。暗黙の連結はパーサが結合
    済み）と、定数の式（f-string・`+`・`*`・`%`・`.format`・`.join`）を組み立てた値。空は除く。

    組み立ては一番外側の式でだけ行う（各ノードを高々定数回しか見ないので線形）。組み立てが上限を
    超える・書式が解析できないときは `_Unanalyzable`。
    """
    inner: set[int] = set()
    for n in ast.walk(node):
        if _assembling(n):
            inner.update(id(c) for c in ast.iter_child_nodes(n))
    assembler = _Assembler()
    out: list[str] = []
    for n in ast.walk(node):
        if id(n) in exempt:
            continue
        text = _constant_text(n)
        if text is None and isinstance(n, _COMPOSITES) and id(n) not in inner:
            text = assembler.assemble(n)
        if text:
            out.append(text)
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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            pairs.append((node.args[1].value, node.args[2]))
        elif isinstance(node, ast.Compare):
            names = _target_names(node.left)
            pairs += [(n, c) for n in names for c in node.comparators]
    return pairs


def _python_hits(tree: ast.AST) -> list[SecretHit]:
    try:
        exempt = _env_key_ids(tree)
        hits: list[SecretHit] = []
        for name, value in _named_values(tree):
            # 名前による検出は形の除外（識別子など）に関係なく効く。環境変数の名前（完全一致の形）
            # だけは読む値の名前なので外す
            if sensitive_name(name) and _literals(value, exempt):
                hits.append(SecretHit(NAME_KIND, 0, 0))
        for literal in _literals(tree, exempt):
            # 値そのものに、形・名前（"password=..." のような文字列）・値の検出をすべてかける。
            # 識別子の除外はしない（文字列の値は識別子ではない）
            found = (
                _pattern_hits(literal)
                + _named_value_hits(literal)
                + _value_runs(literal, allow_identifiers=False)
            )
            hits += [SecretHit(h.kind, 0, 0) for h in found]
        return hits
    except (_Unanalyzable, RecursionError, MemoryError, OverflowError):
        return [SecretHit(UNANALYZABLE_KIND, 0, 0)]


def find_secrets(text: str, *, python: bool = False) -> list[SecretHit]:
    """秘密情報・個人情報らしい値。

    `python=True` なら本文を AST で読み、名前による検出（秘密らしい名前に結びつくリテラル）と
    値による検出（文字列リテラルの中の秘密らしい連なり）を行う（位置は返さない）。読めなければ、また
    `python=False` なら、文字の上で同じ検出を行う（位置つき。引用の伏せ字に使う）。
    """
    # 送る本文そのもの（正規形）に対して検出する。Python ならさらに AST の定数の値にもかける。
    # Python の本文では識別子（`retry_policy_v2`）を値の検出から外す。文字列の値は外さない
    hits = _pattern_hits(text) + _value_runs(text, allow_identifiers=python)
    tree: ast.AST | None = None
    if python:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        except (RecursionError, MemoryError):
            return [*hits, SecretHit(UNANALYZABLE_KIND, 0, len(text))]
    if tree is not None:
        return hits + _python_hits(tree)
    return hits + _named_value_hits(text)


# 関数の単位で値の代わりに置く目印（services/llm_units.redact）と、f-string の差し込み
_MARKER = regex.compile(r"<(?:str|bytes) len=\d{1,12}>|<num>")
_MARKER_OR_FIELD = regex.compile(r"<(?:str|bytes) len=\d{1,12}>|<num>|\{[^{}]{0,256}\}")

_QUOTED_NAMED_VALUE = regex.compile(
    r"([A-Za-z_][A-Za-z0-9_\-]{0,63})[\"']?\s{0,3}[:=]\s{0,3}[rbuRBU]{0,2}([\"'])([^\"'\n]{1,256})\2"
)


def prompt_secret_kinds(prompt: str) -> list[str]:
    """LLM に送る直前のプロンプト全体（規約・チェック項目・symbol・本文）の検出（二重の門）。

    形と値の検出に加え、秘密らしい名前に引用符つきの値が結びつくもの（`password = "..."`）を見る。
    引用符の無い `token = make_token()` は値ではないので見ない。
    """
    hits = _pattern_hits(prompt) + _value_runs(prompt, allow_identifiers=True)
    try:
        for m in _QUOTED_NAMED_VALUE.finditer(prompt, timeout=TIMEOUT_SECONDS):
            # 目印と差し込みだけの値（`password = f'<str len=3>{x}'`）は値ではない
            if sensitive_name(m.group(1)) and _MARKER_OR_FIELD.sub("", m.group(3)).strip():
                hits.append(SecretHit(NAME_KIND, m.start(3), m.end(3)))
    except TimeoutError:
        hits += _timed_out(prompt)
    return sorted({h.kind for h in hits})


def secret_kinds(text: str, *, python: bool = False) -> list[str]:
    """検出した種類（重複なし・決まった順）。値そのものは返さない。"""
    return sorted({h.kind for h in find_secrets(text, python=python)})


def redacted_secret_kinds(text: str) -> list[str]:
    """値を目印（`<str len=N>` など）に置き換えた関数の単位の検出（#114 fix round 3）。

    本文に値は無いので、名前による検出（名前に結びつく値）はしない。目印は値ではない。残る名前・構造の
    中の、既知のトークンの形と値らしい連なり（識別子は除く）だけを見る。
    """
    stripped = _MARKER.sub(" ", text)
    return sorted({h.kind for h in _pattern_hits(stripped) + _value_runs(stripped)})


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
    "UNANALYZABLE_KIND",
    "SENSITIVE_NAME_PARTS",
    "VALUE_KIND",
    "MASK",
    "SECRET_REASON",
    "SecretHit",
    "find_secrets",
    "mask_secrets",
    "prompt_secret_kinds",
    "redacted_secret_kinds",
    "secret_kinds",
    "secret_like_value",
    "sensitive_name",
]
