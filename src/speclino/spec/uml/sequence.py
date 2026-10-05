"""シーケンス図（grammar.md 5 章）→ 順序の義務のオートマトン（contains_subsequence）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from speclino.domain.expr import Expr, Nary, Not
from speclino.domain.uml import (
    Automaton,
    AutomatonTransition,
    CallLabel,
    EpsilonLabel,
    SourceSpan,
)
from speclino.spec.uml._parse import Ctx, Cursor, LineError, register_id, take_bracket_id
from speclino.spec.uml.guard import parse_guard
from speclino.spec.uml.limits import MAX_NESTING
from speclino.spec.uml.types import DiagramResult, Token

_UNSUPPORTED = frozenset(
    {
        "par",
        "and",
        "critical",
        "option",
        "break",
        "rect",
        "Note",
        "note",
        "actor",
        "activate",
        "deactivate",
        "autonumber",
        "box",
        "create",
        "destroy",
        "title",
        "link",
        "links",
        "accTitle",
        "accDescr",
    }
)


@dataclass
class Msg:
    id: str
    operation: str
    span: SourceSpan


@dataclass
class Branch:
    id: str
    guard: Expr | None
    span: SourceSpan
    body: list[Item] = field(default_factory=list)


@dataclass
class Block:
    kind: str  # alt / opt / loop
    branches: list[Branch]
    open_tok: Token


Item = Msg | Block


class _State:
    def __init__(self) -> None:
        self.participants: dict[str, str] = {}
        self.ids: dict[str, Token] = {}
        self.root: list[Item] = []
        self.stack: list[Block] = []

    def body(self) -> list[Item]:
        return self.stack[-1].branches[-1].body if self.stack else self.root


def parse_sequence(ctx: Ctx, lines: list[list[Token]], result: DiagramResult) -> None:
    st = _State()
    for toks in lines:
        ctx.run(toks, lambda cur: _line(ctx, cur, st, result))
    for block in st.stack:
        ctx.error("E_UNCLOSED_BLOCK", f"{block.kind} の end が無い", block.open_tok)
    result.element_ids.update(st.ids)
    if ctx.diagnostics:
        return
    result.automaton = _build(st.root)


def _participant_class(st: _State, tok: Token) -> str:
    if tok.text not in st.participants:
        raise LineError("E_UNDECLARED_PARTICIPANT", f"参加者 {tok.text} が未宣言", tok)
    return st.participants[tok.text]


def _line(ctx: Ctx, cur: Cursor, st: _State, result: DiagramResult) -> None:
    head = cur.peek()
    assert head is not None
    word = head.text if head.kind == "NAME" else ""
    nxt = cur.peek(1)
    is_message = nxt is not None and nxt.kind == "ARROW"
    if word == "participant":
        cur.take("ir")
        alias = cur.take_name("ir", "参加者")
        cls = alias
        if cur.at("as"):
            cur.take("ir")
            cls = cur.take_name("ir", "クラス名")
        if alias.text in st.participants:
            raise LineError("E_DUPLICATE_ID", f"参加者 {alias.text} が重複", alias)
        st.participants[alias.text] = cls.text
        return
    if word in ("alt", "opt", "loop") and not is_message:
        cur.take("ir")
        id_tok = take_bracket_id(cur)
        register_id(st.ids, id_tok)
        guard: Expr | None = None
        if word == "loop":
            if not cur.at_end():
                raise LineError(
                    "E_GUARD_SYNTAX", "loop にはガードを書けない（0 回以上の繰り返し）", cur.peek()
                )
        elif not cur.at_end() or word == "alt":
            guard = parse_guard(cur)
            result.guards.append((guard, ctx.at(id_tok)))
        if len(st.stack) >= MAX_NESTING:
            raise LineError(
                "E_NESTING_TOO_DEEP", f"alt/opt/loop の入れ子は {MAX_NESTING} 段まで", head
            )
        st.stack.append(Block(word, [Branch(id_tok.text, guard, ctx.at(id_tok))], head))
        result.referenceable.add(id_tok.text)
        return
    if word == "else" and not is_message:
        if not st.stack or st.stack[-1].kind != "alt":
            raise LineError("E_UNEXPECTED_TOKEN", "else は alt の中だけ", head)
        cur.take("ir")
        id_tok = take_bracket_id(cur)
        register_id(st.ids, id_tok)
        guard = None
        if not cur.at_end():
            guard = parse_guard(cur)
            result.guards.append((guard, ctx.at(id_tok)))
        st.stack[-1].branches.append(Branch(id_tok.text, guard, ctx.at(id_tok)))
        result.referenceable.add(id_tok.text)
        return
    if word == "end" and not is_message:
        if not st.stack:
            raise LineError("E_UNEXPECTED_TOKEN", "対応する alt/opt/loop が無い end", head)
        cur.take("ir")
        block = st.stack.pop()
        if block.kind == "loop" and not block.branches[0].body:
            raise LineError("E_EMPTY_BLOCK", "loop の本体が空", block.open_tok)
        for b in block.branches[1:-1]:
            if b.guard is None:
                raise LineError("E_GUARD_SYNTAX", "ガードを省けるのは最後の else だけ", head)
        st.body().append(block)
        return
    if word in _UNSUPPORTED or not is_message:
        raise LineError(
            "E_UNSUPPORTED_SYNTAX", f"シーケンス図で対応しない構文: {head.text!r}", head
        )
    sender = cur.take_name("ignored", "送り手")
    _participant_class(st, sender)
    arrow = cur.take("ir", kind="ARROW")
    if arrow.text not in ("->>", "-->>"):
        raise LineError("E_UNKNOWN_ARROW", f"矢印 {arrow.text} は使えない（->> か -->>）", arrow)
    receiver = cur.take_name("ir", "受け手")
    receiver_class = _participant_class(st, receiver)
    cur.take("ir", text=":", what="`:`", code="E_MISSING_ID")
    id_tok = take_bracket_id(cur)
    register_id(st.ids, id_tok)
    if arrow.text == "-->>":
        # 応答は IR に入らない（grammar.md 10 章）
        cur.take_rest("ignored")
        cur.coverage.mark(cur.tokens, "ignored")
        return
    method = cur.take_name("ir", "メソッド名")
    if cur.at("("):
        cur.take("ignored")
        if not cur.at(")"):
            cur.take_name("ignored", "引数名")
            while cur.at(","):
                cur.take("ignored")
                cur.take_name("ignored", "引数名")
        cur.take("ignored", text=")", what="`)`")
    st.body().append(Msg(id_tok.text, f"{receiver_class}.{method.text}", ctx.at(id_tok)))
    result.referenceable.add(id_tok.text)


def complement(guards: list[Expr]) -> Expr:
    """前のガードのどれも成り立たない、の式。"""
    if len(guards) == 1:
        return Not(op="not", arg=guards[0])
    return Not(op="not", arg=Nary(op="or", args=list(guards)))


class _Builder:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.transitions: list[AutomatonTransition] = []

    def new(self) -> str:
        s = f"s{len(self.states)}"
        self.states.append(s)
        return s

    def eps(self, id_: str, src: str, dst: str, guard: Expr | None, span: SourceSpan) -> None:
        self.transitions.append(
            AutomatonTransition(
                id=id_, source=src, target=dst, label=EpsilonLabel(), guard=guard, span=span
            )
        )

    def items(self, items: list[Item], cur: str) -> str:
        for it in items:
            cur = self.item(it, cur)
        return cur

    def item(self, it: Item, cur: str) -> str:
        if isinstance(it, Msg):
            nxt = self.new()
            self.transitions.append(
                AutomatonTransition(
                    id=it.id,
                    source=cur,
                    target=nxt,
                    label=CallLabel(operation=it.operation),
                    span=it.span,
                )
            )
            return nxt
        first = it.branches[0]
        if it.kind == "loop":
            head = self.new()
            self.eps(first.id, cur, head, None, first.span)
            end = self.items(first.body, head)
            self.eps(f"{first.id}.back", end, head, None, first.span)
            join = self.new()
            self.eps(f"{first.id}.exit", head, join, None, first.span)
            return join
        if it.kind == "opt":
            start = self.new()
            self.eps(first.id, cur, start, first.guard, first.span)
            end = self.items(first.body, start)
            join = self.new()
            self.eps(f"{first.id}.join", end, join, None, first.span)
            skip = complement([first.guard]) if first.guard is not None else None
            self.eps(f"{first.id}.skip", cur, join, skip, first.span)
            return join
        # alt
        ends: list[tuple[Branch, str]] = []
        prev: list[Expr] = []
        for b in it.branches:
            guard = b.guard if b.guard is not None else complement(prev)
            start = self.new()
            self.eps(b.id, cur, start, guard, b.span)
            ends.append((b, self.items(b.body, start)))
            if b.guard is not None:
                prev.append(b.guard)
        join = self.new()
        for b, end in ends:
            self.eps(f"{b.id}.join", end, join, None, b.span)
        if it.branches[-1].guard is not None:
            self.eps(f"{first.id}.none", cur, join, complement(prev), first.span)
        return join


def _build(root: list[Item]) -> Automaton:
    b = _Builder()
    start = b.new()
    end = b.items(root, start)
    return Automaton(states=b.states, initial=start, accepting=[end], transitions=b.transitions)


__all__ = ["complement", "parse_sequence"]
