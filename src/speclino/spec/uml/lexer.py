"""行単位の字句解析（grammar.md 2 章）。全域で失敗しない: どんな文字列もトークン列にする。"""

from __future__ import annotations

import re

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
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_INT_RE = re.compile(r"[0-9]+")
_TEXT_RE = re.compile(r"[^\x00-\x7f]+")


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
        m = _NAME_RE.match(text, i) or None
        if m:
            tokens.append(Token("NAME", m.group(), line, col))
            i = m.end()
            continue
        m = _INT_RE.match(text, i)
        if m:
            tokens.append(Token("INT", m.group(), line, col))
            i = m.end()
            continue
        m = _TEXT_RE.match(text, i)
        if m:
            tokens.append(Token("TEXT", m.group(), line, col))
            i = m.end()
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
    """Mermaid ブロックの 1 行。`%%@` は宣言、`%%{` は directive、ほかの `%%` はコメント（行全体で 1 トークン）。"""
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
