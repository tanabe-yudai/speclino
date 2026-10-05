"""行パーサの共通部: トークンを読み進めながら分類（ir / ignored）を付ける。"""

from __future__ import annotations

from speclino.spec.uml.types import Classification, Coverage, Token, TokenKind


class LineError(Exception):
    """1 行の解析の失敗。呼び出し側がその行のトークンをすべて error に分類し、診断にする。"""

    def __init__(self, code: str, message: str, token: Token | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.token = token


class Cursor:
    def __init__(self, tokens: list[Token], coverage: Coverage) -> None:
        self.tokens = tokens
        self.coverage = coverage
        self.i = 0

    def peek(self, offset: int = 0) -> Token | None:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else None

    def at(self, text: str, offset: int = 0) -> bool:
        t = self.peek(offset)
        return t is not None and t.text == text and t.kind != "TEXT"

    def at_end(self) -> bool:
        return self.i >= len(self.tokens)

    def last(self) -> Token | None:
        return self.tokens[self.i - 1] if self.i > 0 else (self.tokens[0] if self.tokens else None)

    def take(
        self,
        cls: Classification,
        *,
        kind: TokenKind | None = None,
        text: str | None = None,
        what: str = "",
        code: str = "E_UNEXPECTED_TOKEN",
    ) -> Token:
        t = self.peek()
        if t is None:
            raise LineError(code, f"{what or text or kind} が必要だが行が終わった", self.last())
        if (kind is not None and t.kind != kind) or (text is not None and t.text != text):
            raise LineError(code, f"{what or text or kind} が必要だが {t.text!r} がある", t)
        self.coverage.mark([t], cls)
        self.i += 1
        return t

    def take_name(self, cls: Classification, what: str, *, dotted: bool = False) -> Token:
        t = self.take(cls, kind="NAME", what=what)
        if not dotted and "." in t.text:
            raise LineError("E_UNEXPECTED_TOKEN", f"{what} にドットは使えない: {t.text}", t)
        return t

    def take_rest(self, cls: Classification) -> list[Token]:
        rest = self.tokens[self.i :]
        self.coverage.mark(rest, cls)
        self.i = len(self.tokens)
        return rest

    def expect_end(self) -> None:
        t = self.peek()
        if t is not None:
            raise LineError("E_UNEXPECTED_TOKEN", f"行末に余分なトークン {t.text!r}", t)


def take_bracket_id(cur: Cursor) -> Token:
    """`[ ID ]` を読み、ID のトークンを返す（要素 ID はドットを含まない NAME）。"""
    cur.take("ir", text="[", what="要素 ID の `[`", code="E_MISSING_ID")
    t = cur.take_name("ir", "要素 ID")
    cur.take("ir", text="]", what="要素 ID の `]`", code="E_MISSING_ID")
    return t


__all__ = ["Cursor", "LineError", "take_bracket_id"]
