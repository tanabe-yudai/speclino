"""行単位の字句解析（grammar.md 2 章）。全域で失敗しない: どんな文字列もトークン列にする。"""

from __future__ import annotations

from collections.abc import Callable

from speclino.spec.uml.types import Token, TokenKind

# 長い順に並べて最長一致にする
_ARROWS = (
    "<|--",
    "-->>",
    "-.->",
    "->>",
    "-->",
    "--x",
    "--o",
    "--)",
    "==>",
    "---",
    "..>",
    "-x",
    "-)",
    "->",
    "--",
)
_PUNCTS = (
    "<<",
    ">>",
    "==",
    "!=",
    "<=",
    ">=",
    "[",
    "]",
    "(",
    ")",
    "{",
    "}",
    "|",
    ":",
    ",",
    "*",
    "<",
    ">",
    "+",
    "-",
    "#",
    "~",
)
_SYMBOLS: tuple[tuple[str, TokenKind], ...] = tuple(
    sorted(
        [(a, "ARROW") for a in _ARROWS] + [(p, "PUNCT") for p in _PUNCTS],
        key=lambda s: -len(s[0]),
    )
)
_ALPHA = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_DIGITS = frozenset("0123456789")
_WORD = _ALPHA | _DIGITS


def _scan_name(text: str, i: int) -> int:
    """NAME（ドット区切りの識別子）の終わりの位置。正規表現を使わず 1 回の走査で読む。"""
    n = len(text)
    j = i + 1
    while True:
        while j < n and text[j] in _WORD:
            j += 1
        if j + 1 < n and text[j] == "." and text[j + 1] in _ALPHA:
            j += 2
            continue
        return j


def _scan_while(text: str, i: int, pred: Callable[[str], bool]) -> int:
    n = len(text)
    j = i
    while j < n and pred(text[j]):
        j += 1
    return j


def tokenize_line(text: str, line: int) -> list[Token]:
    """1 行をトークン列にする。column は 1 始まり。空白はトークンにしない。"""
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace() and ch.isascii():
            i += 1
            continue
        col = i + 1
        if ch in _ALPHA:
            j = _scan_name(text, i)
            tokens.append(Token("NAME", text[i:j], line, col))
            i = j
            continue
        if ch in _DIGITS:
            j = _scan_while(text, i, lambda c: c in _DIGITS)
            tokens.append(Token("INT", text[i:j], line, col))
            i = j
            continue
        if not ch.isascii():
            j = _scan_while(text, i, lambda c: not c.isascii())
            tokens.append(Token("TEXT", text[i:j], line, col))
            i = j
            continue
        for sym, kind in _SYMBOLS:
            if text.startswith(sym, i):
                tokens.append(Token(kind, sym, line, col))
                i += len(sym)
                break
        else:
            tokens.append(Token("OTHER", ch, line, col))
            i += 1
    return tokens


def tokenize_block_line(text: str, line: int) -> list[Token]:
    """Mermaid ブロックの 1 行。

    `%%@` は宣言、`%%{` は directive、ほかの `%%` はコメント（どれも行全体で 1 トークン）。
    """
    stripped = text.lstrip()
    col = len(text) - len(stripped) + 1
    if stripped.startswith("%%@"):
        return [Token("HEADER", stripped.rstrip(), line, col)]
    if stripped.startswith("%%{"):
        return [Token("DIRECTIVE", stripped.rstrip(), line, col)]
    if stripped.startswith("%%"):
        return [Token("COMMENT", stripped.rstrip(), line, col)]
    return tokenize_line(text, line)


__all__ = ["tokenize_block_line", "tokenize_line"]
