"""UML parser の内部の型（トークン・分類・図ごとの中間結果）。

`CompiledSpec` などの公開の契約は `speclino.domain.uml` にある。ここは parser の段の間だけで使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from speclino.domain.expr import Expr
from speclino.domain.uml import (
    Automaton,
    ClassOperation,
    DiagramKind,
    SourceSpan,
    SpecDiagnostic,
    StateTransition,
)

TokenKind = Literal[
    "NAME", "INT", "ARROW", "PUNCT", "TEXT", "OTHER", "COMMENT", "HEADER", "DIRECTIVE", "CELL"
]
Classification = Literal["ir", "ignored", "error"]

DOC_ID = "(document)"
"""図に属さない位置（要求の表・図 ID が決まらない箇所）の span に使う図 ID。"""


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    line: int
    column: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.line, self.column)


@dataclass
class Coverage:
    """トークンの網羅（設計 4.3「全か無か」）。

    各トークンを ir / ignored / error に分類する。
    """

    tokens: list[Token] = field(default_factory=list)
    marks: dict[tuple[int, int], Classification] = field(default_factory=dict)

    def add(self, tokens: list[Token]) -> None:
        self.tokens.extend(tokens)

    def mark(self, tokens: list[Token], cls: Classification) -> None:
        for t in tokens:
            self.marks[t.key] = cls

    def unclassified(self) -> list[Token]:
        return [t for t in self.tokens if t.key not in self.marks]


@dataclass(frozen=True)
class MermaidBlock:
    fence_line: int
    lines: list[tuple[int, str]]


@dataclass(frozen=True)
class MarkdownError:
    code: str
    message: str
    line: int
    column: int = 1


@dataclass(frozen=True)
class RequirementTable:
    header: list[Token]
    rows: list[list[Token]]


@dataclass(frozen=True)
class MarkdownParts:
    blocks: list[MermaidBlock]
    tables: list[RequirementTable]
    errors: list[MarkdownError]
    table_tokens: list[Token]
    warnings: list[MarkdownError] = field(default_factory=list)


@dataclass
class RawAttribute:
    name: str
    type_name: str
    span: SourceSpan


@dataclass
class RawClass:
    name: str
    stereotype: Literal["class", "enumeration", "module"]
    attributes: list[RawAttribute]
    operations: list[ClassOperation]
    enum_values: list[str]
    span: SourceSpan


@dataclass
class DiagramResult:
    """図 1 つ分の中間結果。図をまたぐ解決（列挙・level・参照）は compile が行う。"""

    diagram_id: str
    kind: DiagramKind
    span: SourceSpan
    referenceable: set[str] = field(default_factory=set)
    """要求の表から参照できる要素 ID（grammar.md 9 章）。"""
    element_ids: set[str] = field(default_factory=set)
    """重複検査の対象になった ID すべて（応答など参照できないものも含む）。"""
    guards: list[tuple[Expr, SourceSpan]] = field(default_factory=list)
    classes: list[RawClass] = field(default_factory=list)
    entry: str | None = None
    entry_span: SourceSpan | None = None
    attribute_span: SourceSpan | None = None
    operations: list[tuple[str, SourceSpan]] = field(default_factory=list)
    """図が使う操作（`<クラス>.<メソッド>`）。クラス図との突き合わせに使う。"""
    class_refs: list[tuple[str, SourceSpan]] = field(default_factory=list)
    """図が使うクラス名（シーケンス図の参加者）。"""
    closed: bool = False
    automaton: Automaton | None = None
    attribute: str | None = None
    states: list[str] = field(default_factory=list)
    initial: str | None = None
    state_transitions: list[StateTransition] = field(default_factory=list)


@dataclass
class DiagramOutcome:
    result: DiagramResult | None
    diagnostics: list[SpecDiagnostic]
