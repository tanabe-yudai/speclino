"""UML（Mermaid の厳格な部分集合）から変換した IR（S63–S64、設計 4–5 章）。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from speclino.domain.common import IRModel
from speclino.domain.expr import Expr
from speclino.domain.spec_ir import RuleLevel, SpecIR

DiagramKind = Literal["class", "sequence", "state", "activity"]
AttrType = Literal["int", "bool", "str", "enum", "opaque_id"]


class SourceSpan(IRModel):
    diagram_id: str = Field(min_length=1)
    line: int = Field(ge=1, description="Markdown 全体での行")
    column: int = Field(ge=1)


class SpecDiagnostic(IRModel):
    code: str = Field(pattern=r"^[EW]_[A-Z_]+$")
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1)
    span: SourceSpan | None = None


class ClassAttribute(IRModel):
    name: str = Field(min_length=1)
    type: AttrType
    enum: str | None = Field(default=None, description="type=enum のときの列挙クラス名")


class ClassOperation(IRModel):
    name: str = Field(min_length=1)
    params: list[str] = Field(default_factory=list)


class ClassDef(IRModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    stereotype: Literal["class", "enumeration", "module"] = "class"
    attributes: list[ClassAttribute] = Field(default_factory=list)
    operations: list[ClassOperation] = Field(default_factory=list)
    enum_values: list[str] = Field(default_factory=list)
    span: SourceSpan


class CallLabel(IRModel):
    kind: Literal["call"] = "call"
    operation: str = Field(min_length=1, description="<参加者のクラス名>.<メソッド名>")


class EpsilonLabel(IRModel):
    kind: Literal["epsilon"] = "epsilon"


TransitionLabel = Annotated[CallLabel | EpsilonLabel, Field(discriminator="kind")]


class AutomatonTransition(IRModel):
    id: str = Field(min_length=1, description="図の要素 ID（メッセージ・辺）")
    source: str
    target: str
    label: TransitionLabel
    guard: Expr | None = None
    span: SourceSpan | None = None


class Automaton(IRModel):
    states: list[str] = Field(min_length=1)
    initial: str
    accepting: list[str]
    transitions: list[AutomatonTransition]

    @model_validator(mode="after")
    def _closed_states(self) -> Automaton:
        known = set(self.states)
        refs = (
            {self.initial, *self.accepting}
            | {t.source for t in self.transitions}
            | {t.target for t in self.transitions}
        )
        if not refs <= known:
            raise ValueError(f"未宣言の状態: {sorted(refs - known)}")
        return self


class TraceObligation(IRModel):
    id: str = Field(min_length=1, description="<図 ID>#<入口の操作>")
    diagram_kind: Literal["sequence", "activity"]
    entry_operation: str = Field(min_length=1)
    mode: Literal["contains_subsequence", "included_in"]
    closed: bool = False
    automaton: Automaton
    level: RuleLevel
    spans: list[SourceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed_activity_only(self) -> TraceObligation:
        if self.closed and self.diagram_kind != "activity":
            raise ValueError("closed はアクティビティ図だけ")
        expected = "contains_subsequence" if self.diagram_kind == "sequence" else "included_in"
        if self.mode != expected:
            raise ValueError(f"{self.diagram_kind} の mode は {expected}")
        return self


class StateTransition(IRModel):
    id: str = Field(min_length=1)
    source: str
    target: str
    event: str | None = None
    guard: Expr | None = None
    span: SourceSpan | None = None


class StateMachine(IRModel):
    id: str = Field(min_length=1)
    attribute: str = Field(min_length=1, description="状態を持つ属性（例 order.status）")
    states: list[str] = Field(min_length=1)
    initial: str
    transitions: list[StateTransition]
    level: RuleLevel

    @model_validator(mode="after")
    def _closed_states(self) -> StateMachine:
        known = set(self.states)
        refs = {self.initial} | {t.source for t in self.transitions}
        refs |= {t.target for t in self.transitions}
        if not refs <= known:
            raise ValueError(f"未宣言の状態: {sorted(refs - known)}")
        return self


class StructureObligation(IRModel):
    id: str = Field(min_length=1, description="<図 ID>#<クラス ID>")
    class_name: str = Field(min_length=1)
    operations: list[ClassOperation] = Field(default_factory=list)
    level: RuleLevel


RelationKind = Literal["inheritance", "composition", "aggregation", "association", "dependency"]


class RelationObligation(IRModel):
    """クラス図の関係線（grammar.md 4.1 節）。source が依存する側（子・全体・使う側）。"""

    id: str = Field(
        min_length=1,
        description="<図 ID>#<関係 ID>、ID が無ければ <図 ID>#<source>-<kind>-<target>",
    )
    element_id: str | None = Field(default=None, description="図に書いた関係 ID（`[r1]`）")
    kind: RelationKind
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    level: RuleLevel
    span: SourceSpan


class RequirementRow(IRModel):
    id: str = Field(min_length=1)
    level: RuleLevel
    text: str = Field(min_length=1)
    formalized_by: list[str] = Field(
        default_factory=list, description="`図ID#要素ID` か `ir:要求ID`"
    )


class CompiledSpec(IRModel):
    parser_version: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ir_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    classes: list[ClassDef] = Field(default_factory=list)
    structure: list[StructureObligation] = Field(default_factory=list)
    relations: list[RelationObligation] = Field(
        default_factory=list,
        # 空なら出力しない: 関係の無い仕様の JSON・ir_hash・content_hash を関係の追加前と同じに保つ
        exclude_if=lambda v: not v,
        description="クラス図の関係線（grammar.md 4.1 節）",
    )
    traces: list[TraceObligation] = Field(default_factory=list)
    state_machines: list[StateMachine] = Field(default_factory=list)
    legacy: list[SpecIR] = Field(default_factory=list)
    requirements: list[RequirementRow] = Field(default_factory=list)


class ParseResult(IRModel):
    compiled: CompiledSpec | None
    diagnostics: list[SpecDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def _all_or_nothing(self) -> ParseResult:
        has_error = any(d.severity == "error" for d in self.diagnostics)
        if has_error == (self.compiled is not None):
            raise ValueError("エラーがあれば compiled は None、無ければ必須（全か無か）")
        return self


__all__ = [
    "AttrType",
    "Automaton",
    "AutomatonTransition",
    "CallLabel",
    "ClassAttribute",
    "ClassDef",
    "ClassOperation",
    "CompiledSpec",
    "DiagramKind",
    "EpsilonLabel",
    "ParseResult",
    "RelationKind",
    "RelationObligation",
    "RequirementRow",
    "SourceSpan",
    "SpecDiagnostic",
    "StateMachine",
    "StateTransition",
    "StructureObligation",
    "TraceObligation",
    "TransitionLabel",
]
