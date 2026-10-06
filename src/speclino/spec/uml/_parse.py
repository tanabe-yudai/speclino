"""行パーサの共通部: トークンを読み進めながら分類（ir / ignored）を付ける。"""

from __future__ import annotations

from collections.abc import Callable

from speclino.domain.uml import SourceSpan, SpecDiagnostic
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


class Ctx:
    """図 1 つを解析する間の文脈（図 ID・網羅・診断）。"""

    def __init__(self, diagram_id: str, coverage: Coverage) -> None:
        self.diagram_id = diagram_id
        self.coverage = coverage
        self.diagnostics: list[SpecDiagnostic] = []

    def at(self, tok: Token) -> SourceSpan:
        return SourceSpan(diagram_id=self.diagram_id, line=tok.line, column=tok.column)

    def span(self, tok: Token | SourceSpan | None) -> SourceSpan | None:
        if tok is None or isinstance(tok, SourceSpan):
            return tok
        return self.at(tok)

    def error(self, code: str, message: str, tok: Token | SourceSpan | None) -> None:
        self.diagnostics.append(
            SpecDiagnostic(code=code, severity="error", message=message, span=self.span(tok))
        )

    def warn(self, code: str, message: str, tok: Token | SourceSpan | None) -> None:
        self.diagnostics.append(
            SpecDiagnostic(code=code, severity="warning", message=message, span=self.span(tok))
        )

    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def run(self, tokens: list[Token], fn: Callable[[Cursor], None]) -> bool:
        """1 行を解析する。失敗したらその行のトークンをすべて error に分類して診断を残す。"""
        cur = Cursor(tokens, self.coverage)
        try:
            fn(cur)
            cur.expect_end()
        except LineError as e:
            self.coverage.mark(tokens, "error")
            self.error(e.code, e.message, e.token)
            return False
        return True


def register_id(ids: dict[str, Token], tok: Token) -> None:
    """図の中の要素 ID を登録する。重複（ラベルだけ違う同じ ID を含む）はエラー。"""
    if tok.text in ids:
        first = ids[tok.text]
        raise LineError(
            "E_DUPLICATE_ID",
            f"ID {tok.text} が重複している（最初は {first.line} 行目）",
            tok,
        )
    ids[tok.text] = tok


DIRECTIONS = frozenset({"TB", "TD", "BT", "LR", "RL"})

RESERVED_SEQUENCE = frozenset(
    {
        "end",
        "alt",
        "else",
        "opt",
        "loop",
        "par",
        "and",
        "rect",
        "critical",
        "break",
        "option",
        "note",
        "Note",
        "participant",
        "actor",
        "activate",
        "deactivate",
        "autonumber",
        "box",
        "create",
        "destroy",
        "title",
        "as",
        "over",
        "left",
        "right",
        "of",
    }
)
"""シーケンス図の名前に使うと Mermaid の描画が崩れる語（W_RESERVED_WORD）。"""
RESERVED_FLOWCHART = frozenset(
    {
        "end",
        "graph",
        "flowchart",
        "subgraph",
        "style",
        "classDef",
        "class",
        "click",
        "linkStyle",
        "direction",
        "default",
    }
)
"""flowchart のノード ID に使うと Mermaid の描画が崩れる語（W_RESERVED_WORD）。"""


__all__ = [
    "DIRECTIONS",
    "RESERVED_FLOWCHART",
    "RESERVED_SEQUENCE",
    "Ctx",
    "Cursor",
    "LineError",
    "register_id",
    "take_bracket_id",
]
