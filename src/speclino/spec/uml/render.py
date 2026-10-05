"""`CompiledSpec` → 正規形の Markdown（設計 4.3 の往復、grammar.md 11 章）。

`compile_spec` の出力だけを対象にする（オートマトンは 5.1 節の構成の形をしている前提）。
正規形では見た目だけの要素（コメント・別名・送り手・応答・判断ノードのラベル・可視性）は落ちる。
旧形式の IR は本文に書かない（呼び出し側が同じ `legacy_ir` を渡す）。
"""

from __future__ import annotations

from speclino.domain.uml import (
    Automaton,
    AutomatonTransition,
    CallLabel,
    ClassDef,
    CompiledSpec,
    StateMachine,
    TraceObligation,
)
from speclino.spec.uml.guard import render_expr
from speclino.spec.uml.markdown import REQ_HEADER


def _fence(lines: list[str]) -> str:
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


def _requirements(compiled: CompiledSpec) -> str:
    out = ["| " + " | ".join(REQ_HEADER) + " |", "| --- | --- | --- | --- |"]
    for r in compiled.requirements:
        refs = ", ".join(r.formalized_by) if r.formalized_by else "-"
        out.append(f"| {r.id} | {r.level} | {r.text} | {refs} |")
    return "\n".join(out) + "\n"


def _class_lines(c: ClassDef) -> list[str]:
    members: list[str] = []
    if c.stereotype == "enumeration":
        members.append("        <<enumeration>>")
        members.extend(f"        {v}" for v in c.enum_values)
    for a in c.attributes:
        type_name = a.enum if a.type == "enum" else a.type
        members.append(f"        {type_name} {a.name}")
    for op in c.operations:
        members.append(f"        {op.name}({', '.join(op.params)})")
    if not members:
        return [f"    class {c.name}"]
    return [f"    class {c.name} {{", *members, "    }"]


def _class_diagrams(classes: list[ClassDef]) -> list[str]:
    groups: dict[str, list[ClassDef]] = {}
    for c in classes:
        groups.setdefault(c.span.diagram_id, []).append(c)
    return [
        _fence(
            [f"%%@ id: {diagram_id}", "classDiagram"]
            + [line for c in members for line in _class_lines(c)]
        )
        for diagram_id, members in groups.items()
    ]


# ---------------------------------------------------------------- シーケンス図


class _SeqRenderer:
    def __init__(self, a: Automaton, sender: str) -> None:
        self.sender = sender
        self.out: dict[str, list[AutomatonTransition]] = {}
        self.by_id: dict[str, AutomatonTransition] = {}
        for t in a.transitions:
            self.out.setdefault(t.source, []).append(t)
            self.by_id[t.id] = t
        self.lines: list[str] = []

    def walk(self, state: str, indent: int) -> str:
        """`state` から本体を書き出し、本体の終わりの状態を返す。"""
        pad = "    " * indent
        while True:
            forward = [t for t in self.out.get(state, []) if "." not in t.id]
            if not forward:
                return state
            first = forward[0]
            if isinstance(first.label, CallLabel):
                cls, method = first.label.operation.split(".")
                self.lines.append(f"{pad}{self.sender}->>{cls}: [{first.id}] {method}")
                state = first.target
                continue
            skip = self.by_id.get(f"{first.id}.skip")
            exit_ = self.by_id.get(f"{first.id}.exit")
            if skip is not None:
                guard = f" {render_expr(first.guard)}" if first.guard is not None else ""
                self.lines.append(f"{pad}opt [{first.id}]{guard}")
                self.walk(first.target, indent + 1)
                self.lines.append(f"{pad}end")
                state = skip.target
            elif exit_ is not None:
                self.lines.append(f"{pad}loop [{first.id}]")
                self.walk(first.target, indent + 1)
                self.lines.append(f"{pad}end")
                state = exit_.target
            else:
                state = self._alt(forward, pad, indent)

    def _alt(self, branches: list[AutomatonTransition], pad: str, indent: int) -> str:
        has_none = f"{branches[0].id}.none" in self.by_id
        for i, b in enumerate(branches):
            bare = i == len(branches) - 1 and i > 0 and not has_none
            keyword = "alt" if i == 0 else "else"
            assert b.guard is not None
            guard = "" if bare else f" {render_expr(b.guard)}"
            self.lines.append(f"{pad}{keyword} [{b.id}]{guard}")
            self.walk(b.target, indent + 1)
        self.lines.append(f"{pad}end")
        return self.by_id[f"{branches[0].id}.join"].target


def _sequence(t: TraceObligation) -> str:
    diagram_id = t.id.split("#", 1)[0]
    sender = t.entry_operation.split(".")[0]
    participants = [sender]
    for tr in t.automaton.transitions:
        if isinstance(tr.label, CallLabel):
            cls = tr.label.operation.split(".")[0]
            if cls not in participants:
                participants.append(cls)
    r = _SeqRenderer(t.automaton, sender)
    r.walk(t.automaton.initial, 1)
    return _fence(
        [f"%%@ id: {diagram_id}", f"%%@ entry: {t.entry_operation}", "sequenceDiagram"]
        + [f"    participant {p}" for p in participants]
        + r.lines
    )


# ---------------------------------------------------------------- アクティビティ図・状態遷移図


def _activity(t: TraceObligation) -> str:
    a = t.automaton
    diagram_id = t.id.split("#", 1)[0]
    ops: dict[str, str] = {}
    for tr in a.transitions:
        if isinstance(tr.label, CallLabel):
            ops[tr.target] = tr.label.operation
    lines = [f"%%@ id: {diagram_id}", f"%%@ entry: {t.entry_operation}"]
    if t.closed:
        lines.append("%%@ closed: true")
    lines.append("flowchart TD")
    accepting = set(a.accepting)
    for s in a.states:
        if s == a.initial:
            lines.append(f"    {s}((start))")
        elif s in accepting:
            lines.append(f"    {s}((end))")
        elif s in ops:
            lines.append(f"    {s}[{ops[s]}]")
        else:
            lines.append(f"    {s}{{{s}}}")
    for tr in a.transitions:
        guard = f" {render_expr(tr.guard)}" if tr.guard is not None else ""
        lines.append(f'    {tr.source} -->|"[{tr.id}]{guard}"| {tr.target}')
    return _fence(lines)


def _state_machine(m: StateMachine) -> str:
    lines = [f"%%@ id: {m.id}", f"%%@ attribute: {m.attribute}", "stateDiagram-v2"]
    lines.append(f"    [*] --> {m.initial}")
    for t in m.transitions:
        label = f"[{t.id}]"
        if t.event is not None:
            label += f" {t.event}"
        if t.guard is not None:
            label += f" [{render_expr(t.guard)}]"
        lines.append(f"    {t.source} --> {t.target}: {label}")
    return _fence(lines)


def render(compiled: CompiledSpec) -> str:
    """正規形の Markdown。`compile_spec(render(c))` は c と意味の正規形で一致する。"""
    parts: list[str] = []
    if compiled.requirements:
        parts.append(_requirements(compiled))
    parts.extend(_class_diagrams(compiled.classes))
    for t in compiled.traces:
        parts.append(_sequence(t) if t.diagram_kind == "sequence" else _activity(t))
    parts.extend(_state_machine(m) for m in compiled.state_machines)
    return "\n".join(parts)


__all__ = ["render"]
