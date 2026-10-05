"""アクティビティ図（flowchart、grammar.md 7 章）→ 制御フローのオートマトン（included_in）。"""

from __future__ import annotations

from dataclasses import dataclass

from speclino.domain.expr import Expr
from speclino.domain.uml import (
    Automaton,
    AutomatonTransition,
    CallLabel,
    EpsilonLabel,
    SourceSpan,
    TransitionLabel,
)
from speclino.spec.uml._parse import (
    RESERVED_FLOWCHART,
    Ctx,
    Cursor,
    LineError,
    register_id,
    take_bracket_id,
)
from speclino.spec.uml.guard import parse_guard
from speclino.spec.uml.types import DiagramResult, Token

_UNSUPPORTED = frozenset(
    {"subgraph", "click", "style", "classDef", "class", "linkStyle", "graph", "direction"}
)


@dataclass
class _Node:
    kind: str  # process / decision / start / end
    operation: str | None
    tok: Token


@dataclass
class _Edge:
    id: str
    source: str
    target: str
    guard: Expr | None
    span: SourceSpan


class _State:
    def __init__(self) -> None:
        self.nodes: dict[str, _Node] = {}
        self.ids: dict[str, Token] = {}
        self.order: dict[str, None] = {}  # 最初に現れた順（dict は挿入順）
        self.refs: list[Token] = []
        self.edges: list[_Edge] = []

    def see(self, tok: Token) -> None:
        if tok.text not in self.order:
            self.order[tok.text] = None
        self.refs.append(tok)


def parse_activity(ctx: Ctx, lines: list[list[Token]], result: DiagramResult) -> None:
    st = _State()
    for toks in lines:
        ctx.run(toks, lambda cur: _line(ctx, cur, st, result))
    if ctx.has_errors():
        return
    for ref in st.refs:
        if ref.text not in st.nodes:
            ctx.error("E_UNDECLARED_NODE", f"ノード {ref.text} の形が宣言されていない", ref)
            return
    starts = [n for n, v in st.nodes.items() if v.kind == "start"]
    ends = [n for n, v in st.nodes.items() if v.kind == "end"]
    if len(starts) != 1:
        ctx.error("E_ACTIVITY_START", f"開始ノードはちょうど 1 つ（{len(starts)} 個）", result.span)
    if not ends:
        ctx.error("E_ACTIVITY_END", "終了ノードが無い", result.span)
    incoming = {e.target for e in st.edges}
    outgoing = {e.source for e in st.edges}
    for name in st.order:
        node = st.nodes[name]
        if node.kind == "start" and name in incoming:
            ctx.error("E_ACTIVITY_START", f"開始ノード {name} に入る辺がある", node.tok)
        if node.kind == "end" and name in outgoing:
            ctx.error("E_ACTIVITY_END", f"終了ノード {name} から出る辺がある", node.tok)
        if node.kind != "start" and name not in incoming:
            ctx.error("E_UNREACHABLE", f"ノード {name} に入る辺が無い", node.tok)
        if node.kind != "end" and name not in outgoing:
            ctx.error("E_DEAD_END", f"ノード {name} から出る辺が無い", node.tok)
    if not any(n.kind == "process" for n in st.nodes.values()):
        ctx.error("E_EMPTY_DIAGRAM", "アクティビティ図に処理ノードが無い", result.span)
    if ctx.has_errors():
        return
    for n in st.nodes.values():
        if n.operation is not None:
            result.operations.append((n.operation, ctx.at(n.tok)))
    transitions: list[AutomatonTransition] = []
    for e in st.edges:
        target = st.nodes[e.target]
        label: TransitionLabel = (
            CallLabel(operation=target.operation)
            if target.operation is not None
            else EpsilonLabel()
        )
        transitions.append(
            AutomatonTransition(
                id=e.id, source=e.source, target=e.target, label=label, guard=e.guard, span=e.span
            )
        )
    result.automaton = Automaton(
        states=list(st.order),
        initial=starts[0],
        accepting=[n for n in st.order if st.nodes[n].kind == "end"],
        transitions=transitions,
    )
    result.referenceable.update(st.ids)
    result.element_ids.update(st.ids)


def _operation(cur: Cursor) -> str:
    t = cur.take("ir", kind="NAME", what="<クラス名>.<メソッド名>", code="E_ACTIVITY_LABEL")
    if t.text.count(".") != 1:
        raise LineError(
            "E_ACTIVITY_LABEL", f"処理ノードのラベルは <クラス名>.<メソッド名>: {t.text!r}", t
        )
    return t.text


def _node(ctx: Ctx, cur: Cursor, st: _State) -> Token:
    tok = cur.take_name("ir", "ノード ID")
    if tok.text in _UNSUPPORTED:
        raise LineError(
            "E_UNSUPPORTED_SYNTAX", f"アクティビティ図で対応しない構文: {tok.text!r}", tok
        )
    st.see(tok)
    node: _Node | None = None
    if cur.at("["):
        cur.take("ignored")
        node = _Node("process", _operation(cur), tok)
        cur.take("ignored", text="]", what="`]`")
    elif cur.at("(") and cur.at("(", 1):
        cur.take("ir")
        cur.take("ir")
        kw = cur.take("ir", kind="NAME", what="start か end")
        if kw.text not in ("start", "end"):
            raise LineError("E_ACTIVITY_LABEL", "円のノードは ((start)) か ((end))", kw)
        cur.take("ir", text=")", what="`)`")
        cur.take("ir", text=")", what="`)`")
        node = _Node(kw.text, None, tok)
    elif cur.at("(") and cur.at("[", 1):
        cur.take("ignored")
        cur.take("ignored")
        node = _Node("process", _operation(cur), tok)
        cur.take("ignored", text="]", what="`]`")
        cur.take("ignored", text=")", what="`)`")
    elif cur.at("("):
        cur.take("ignored")
        node = _Node("process", _operation(cur), tok)
        cur.take("ignored", text=")", what="`)`")
    elif cur.at("{"):
        cur.take("ir")
        while not cur.at("}"):
            if cur.at_end():
                raise LineError("E_UNEXPECTED_TOKEN", "判断ノードの `}` が無い", tok)
            cur.take("ignored")
        cur.take("ir")
        node = _Node("decision", None, tok)
    if node is not None:
        if tok.text in RESERVED_FLOWCHART:
            ctx.warn("W_RESERVED_WORD", f"{tok.text} は Mermaid の予約語で描画が崩れる", tok)
        register_id(st.ids, tok)
        st.nodes[tok.text] = node
    return tok


def _line(ctx: Ctx, cur: Cursor, st: _State, result: DiagramResult) -> None:
    head = cur.peek()
    assert head is not None
    if head.kind != "NAME":
        raise LineError(
            "E_UNSUPPORTED_SYNTAX", f"アクティビティ図で対応しない構文: {head.text!r}", head
        )
    before = len(st.nodes)
    source = _node(ctx, cur, st)
    if cur.at_end():
        if len(st.nodes) == before:
            raise LineError("E_UNEXPECTED_TOKEN", f"ノード {source.text} の形が無い行", source)
        return
    arrow = cur.take("ir", kind="ARROW", what="-->")
    if arrow.text != "-->":
        raise LineError("E_UNKNOWN_ARROW", f"矢印 {arrow.text} は使えない（-->）", arrow)
    cur.take("ir", text="|", what="辺の `|[ID]|`", code="E_MISSING_ID")
    id_tok = take_bracket_id(cur)
    register_id(st.ids, id_tok)
    guard = None
    if not cur.at("|"):
        guard = parse_guard(cur)
        result.guards.append((guard, ctx.at(id_tok)))
    cur.take("ir", text="|", what="辺のラベルの `|`")
    target = _node(ctx, cur, st)
    nxt = cur.peek()
    if nxt is not None and nxt.kind == "ARROW":
        raise LineError("E_UNEXPECTED_TOKEN", "1 行に辺は 1 本（それぞれに ID が要る）", nxt)
    st.edges.append(_Edge(id_tok.text, source.text, target.text, guard, ctx.at(id_tok)))


__all__ = ["parse_activity"]
