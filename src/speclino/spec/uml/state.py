"""状態遷移図（grammar.md 6 章）→ 遷移の関係（StateMachine）。"""

from __future__ import annotations

from speclino.domain.uml import StateTransition
from speclino.spec.uml._parse import (
    DIRECTIONS,
    Ctx,
    Cursor,
    LineError,
    register_id,
    take_bracket_id,
)
from speclino.spec.uml.guard import parse_guard
from speclino.spec.uml.types import Classification, DiagramResult, Token


class _State:
    def __init__(self) -> None:
        self.initial: Token | None = None
        self.order: dict[str, None] = {}  # 最初に現れた順（dict は挿入順）
        self.ids: dict[str, Token] = {}
        self.finals: list[Token] = []

    def see(self, name: str) -> None:
        if name not in self.order:
            self.order[name] = None


def parse_state(ctx: Ctx, lines: list[list[Token]], result: DiagramResult) -> None:
    st = _State()
    for toks in lines:
        ctx.run(toks, lambda cur: _line(ctx, cur, st, result))
    if st.initial is None:
        ctx.error("E_STATE_INITIAL", "初期状態（`[*] --> X`）が無い", None)
        return
    for f in st.finals:
        if f.text not in st.order and f.text != st.initial.text:
            ctx.error("E_UNKNOWN_STATE", f"状態 {f.text} は遷移に現れない", f)
    for name in [st.initial.text, *st.order]:
        if name in st.ids:
            ctx.error("E_DUPLICATE_ID", f"状態名 {name} が遷移 ID と重複", st.ids[name])
    if not ctx.has_errors() and not result.state_transitions:
        ctx.error("E_EMPTY_DIAGRAM", "状態遷移図に遷移が無い", st.initial)
    if ctx.has_errors():
        return
    initial = st.initial.text
    result.initial = initial
    result.states = [initial, *[s for s in st.order if s != initial]]
    result.referenceable.update(result.states)
    result.referenceable.update(st.ids)
    result.element_ids.update(result.referenceable)


def _star(cur: Cursor, cls: Classification) -> None:
    cur.take(cls, text="[")
    cur.take(cls, text="*")
    cur.take(cls, text="]")


def _line(ctx: Ctx, cur: Cursor, st: _State, result: DiagramResult) -> None:
    head = cur.peek()
    assert head is not None
    if head.text == "direction" and head.kind == "NAME":
        cur.take("ignored")
        d = cur.take("ignored", kind="NAME", what="向き")
        if d.text not in DIRECTIONS:
            raise LineError("E_UNEXPECTED_TOKEN", "向きは TB/TD/BT/LR/RL", d)
        return
    if head.text == "[":
        _star(cur, "ir")
        arrow = cur.take("ir", kind="ARROW", what="-->")
        if arrow.text != "-->":
            raise LineError("E_UNKNOWN_ARROW", f"矢印 {arrow.text} は使えない（-->）", arrow)
        target = cur.take_name("ir", "状態名")
        if st.initial is not None:
            raise LineError("E_STATE_INITIAL", "初期状態が 2 つある", head)
        st.initial = target
        return
    nxt = cur.peek(1)
    if head.kind != "NAME" or nxt is None or nxt.kind != "ARROW":
        raise LineError("E_UNSUPPORTED_SYNTAX", f"状態遷移図で対応しない構文: {head.text!r}", head)
    source = cur.take_name("ir", "状態名")
    arrow = cur.take("ir", kind="ARROW")
    if arrow.text != "-->":
        raise LineError("E_UNKNOWN_ARROW", f"矢印 {arrow.text} は使えない（-->）", arrow)
    if cur.at("["):
        _star(cur, "ignored")
        cur.coverage.mark([source, arrow], "ignored")
        st.finals.append(source)
        return
    target = cur.take_name("ir", "状態名")
    cur.take("ir", text=":", what="遷移の `: [ID]`", code="E_MISSING_ID")
    id_tok = take_bracket_id(cur)
    register_id(st.ids, id_tok)
    event: str | None = None
    if not cur.at_end() and not cur.at("["):
        event = cur.take_name("ir", "イベント名").text
    guard = None
    if cur.at("["):
        cur.take("ir")
        guard = parse_guard(cur)
        cur.take("ir", text="]", what="ガードの `]`", code="E_GUARD_SYNTAX")
        result.guards.append((guard, ctx.at(id_tok)))
    st.see(source.text)
    st.see(target.text)
    result.state_transitions.append(
        StateTransition(
            id=id_tok.text,
            source=source.text,
            target=target.text,
            event=event,
            guard=guard,
            span=ctx.at(id_tok),
        )
    )


__all__ = ["parse_state"]
