"""クラス図（grammar.md 4 章）→ クラスの語彙と関係。型名の解決は compile が図をまたいで行う。"""

from __future__ import annotations

from typing import Literal

from speclino.domain.uml import ClassOperation, RelationKind
from speclino.spec.uml._parse import (
    DIRECTIONS,
    Ctx,
    Cursor,
    LineError,
    register_id,
    take_bracket_id,
)
from speclino.spec.uml.types import DiagramResult, RawAttribute, RawClass, RawRelation, Token

_VISIBILITY = frozenset({"+", "-", "#", "~"})
_STEREOTYPES: dict[str, Literal["enumeration", "module"]] = {
    "enumeration": "enumeration",
    "module": "module",
}


# 書いた向きの関係線 → (種類, 左が source か)。継承だけは右（子）が source
_REL_ARROWS: dict[str, tuple[RelationKind, bool]] = {
    "<|--": ("inheritance", False),
    "*--": ("composition", True),
    "o--": ("aggregation", True),
    "-->": ("association", True),
    "..>": ("dependency", True),
}
_REL_HINT = (
    "関係線は `A <|-- B`・`A *-- B`・`A o-- B`・`A --> B`・`A ..> B` だけ"
    "（逆向き・双方向・向きの無い線は左右を入れ替えて書く）"
)
# 関係の行と紛らわしい Mermaid の文（先頭の語）
_UNSUPPORTED_HEADS = frozenset(
    {"note", "namespace", "style", "classDef", "cssClass", "click", "link", "callback"}
)
# 種類ごとに端に置けるステレオタイプ（source, target）
_ENDS: dict[RelationKind, tuple[frozenset[str], frozenset[str]]] = {
    "inheritance": (frozenset({"class"}), frozenset({"class"})),
    "composition": (frozenset({"class", "module"}), frozenset({"class", "enumeration"})),
    "aggregation": (frozenset({"class", "module"}), frozenset({"class", "enumeration"})),
    "association": (frozenset({"class", "module"}), frozenset({"class", "enumeration"})),
    "dependency": (frozenset({"class", "module"}), frozenset({"class", "enumeration", "module"})),
}


class _State:
    def __init__(self) -> None:
        self.current: RawClass | None = None
        self.current_tok: Token | None = None
        self.class_ids: dict[str, Token] = {}
        self.member_ids: dict[str, Token] = {}
        self.relation_keys: dict[tuple[str, str, str], Token] = {}


def parse_class_diagram(ctx: Ctx, lines: list[list[Token]], result: DiagramResult) -> None:
    st = _State()
    for toks in lines:
        ctx.run(toks, lambda cur: _line(ctx, cur, st, result))
    if st.current is not None:
        ctx.error("E_UNCLOSED_BLOCK", f"class {st.current.name} の `}}` が無い", st.current_tok)
    if not result.classes and not ctx.has_errors():
        ctx.error("E_EMPTY_DIAGRAM", "クラス図にクラスが無い", result.span)
    for cls in result.classes:
        _check_class(ctx, cls, st.class_ids[cls.name])
    _check_relations(ctx, result)


def _check_relations(ctx: Ctx, result: DiagramResult) -> None:
    """関係の両端が同じ図で宣言したクラスで、種類に合うステレオタイプか（4.1 節）。"""
    stereotypes = {c.name: c.stereotype for c in result.classes}
    for rel in result.relations:
        ok = True
        for name, tok in ((rel.source, rel.source_tok), (rel.target, rel.target_tok)):
            if name not in stereotypes:
                ctx.error("E_UNKNOWN_CLASS", f"関係の端 {name} がこの図で宣言されていない", tok)
                ok = False
        if not ok:
            continue
        allowed_src, allowed_tgt = _ENDS[rel.kind]
        for name, tok, allowed, side in (
            (rel.source, rel.source_tok, allowed_src, "source"),
            (rel.target, rel.target_tok, allowed_tgt, "target"),
        ):
            st = stereotypes[name]
            if st not in allowed:
                ctx.error(
                    "E_RELATION_END",
                    f"{rel.kind} の {side} に {st} の {name} は置けない（grammar.md 4.1 節）",
                    tok,
                )


def _check_class(ctx: Ctx, cls: RawClass, tok: Token) -> None:
    if cls.stereotype == "module" and (cls.attributes or cls.enum_values):
        ctx.error("E_MODULE_MEMBER", f"モジュール {cls.name} は属性・列挙値を持てない", tok)
    if cls.stereotype == "enumeration":
        if cls.attributes or cls.operations:
            ctx.error("E_ENUM_MEMBER", f"列挙 {cls.name} は属性・操作を持てない", tok)
        if not cls.enum_values:
            ctx.error("E_ENUM_EMPTY", f"列挙 {cls.name} に値が無い", tok)


def _line(ctx: Ctx, cur: Cursor, st: _State, result: DiagramResult) -> None:
    if st.current is not None:
        _member(ctx, cur, st)
        return
    head = cur.peek()
    assert head is not None
    if head.text == "direction":
        cur.take("ignored")
        d = cur.take("ignored", kind="NAME", what="向き")
        if d.text not in DIRECTIONS:
            raise LineError("E_UNEXPECTED_TOKEN", "向きは TB/TD/BT/LR/RL", d)
        return
    if head.kind == "NAME" and head.text != "class" and head.text not in _UNSUPPORTED_HEADS:
        _relation(ctx, cur, st, result)
        return
    if head.text != "class" or head.kind != "NAME":
        raise LineError(
            "E_UNSUPPORTED_SYNTAX",
            f"クラス図で対応しない構文: {head.text!r}（注記・`Class : member` は書けない）",
            head,
        )
    cur.take("ir")
    name = cur.take_name("ir", "クラス名")
    register_id(st.class_ids, name)
    cls = RawClass(
        name=name.text,
        stereotype="class",
        attributes=[],
        operations=[],
        enum_values=[],
        span=ctx.at(name),
    )
    result.classes.append(cls)
    result.referenceable.add(name.text)
    result.element_ids.add(name.text)
    if cur.at("{"):
        cur.take("ir")
        st.current = cls
        st.current_tok = name
        st.member_ids = {}


def _relation(ctx: Ctx, cur: Cursor, st: _State, result: DiagramResult) -> None:
    left = cur.take_name("ir", "クラス名")
    if cur.at(":"):
        raise LineError("E_UNSUPPORTED_SYNTAX", "`Class : member` の書式は書けない", left)
    _multiplicity(cur)
    arrow = _rel_arrow(cur)
    _multiplicity(cur)
    right = cur.take_name("ir", "クラス名")
    element: Token | None = None
    if cur.at(":"):
        cur.take("ignored")
        if cur.at("["):
            element = take_bracket_id(cur)
        cur.take_rest("ignored")
    kind, left_is_source = _REL_ARROWS[arrow]
    src, tgt = (left, right) if left_is_source else (right, left)
    if element is not None:
        register_id(st.class_ids, element)
        result.referenceable.add(element.text)
        result.element_ids.add(element.text)
    else:
        key = (src.text, kind, tgt.text)
        if key in st.relation_keys:
            first = st.relation_keys[key]
            raise LineError(
                "E_DUPLICATE_ID",
                f"ID の無い同じ関係が重複している（最初は {first.line} 行目）。ID を付けて区別する",
                left,
            )
        st.relation_keys[key] = left
    result.relations.append(
        RawRelation(
            element_id=element.text if element is not None else None,
            kind=kind,
            source=src.text,
            target=tgt.text,
            source_tok=src,
            target_tok=tgt,
            span=ctx.at(left),
        )
    )


def _rel_arrow(cur: Cursor) -> str:
    """関係線を読む。`*--` と `o--` は間を空けない 2 つの字句（grammar.md 2 章）。"""
    t = cur.peek()
    nxt = cur.peek(1)
    if t is not None and t.kind == "ARROW" and t.text in _REL_ARROWS:
        if (
            nxt is not None
            and nxt.kind in ("PUNCT", "ARROW")
            and nxt.column == t.column + len(t.text)
        ):
            raise LineError("E_UNKNOWN_ARROW", _REL_HINT, t)  # `<|--|>` など続きのある線
        cur.take("ir")
        return t.text
    if (
        t is not None
        and nxt is not None
        and ((t.kind == "PUNCT" and t.text == "*") or (t.kind == "NAME" and t.text == "o"))
        and nxt.kind == "ARROW"
        and nxt.text == "--"
        and nxt.line == t.line
        and nxt.column == t.column + 1
    ):
        cur.take("ir")
        cur.take("ir")
        return f"{t.text}--"
    raise LineError("E_UNKNOWN_ARROW", _REL_HINT, t or cur.last())


def _multiplicity(cur: Cursor) -> None:
    """`"1"`・`"0..1"`・`"1..*"` などの多重度（表示だけ。IR に入れない）。"""
    if not (cur.at('"') and (q := cur.peek()) is not None and q.kind == "OTHER"):
        return
    toks = [cur.take("ignored")]
    while True:
        t = cur.peek()
        if t is None:
            raise LineError("E_UNEXPECTED_TOKEN", '多重度の閉じる `"` が無い', toks[0])
        cur.take("ignored")
        if t.kind == "OTHER" and t.text == '"':
            break
        toks.append(t)
    text = "".join(t.text for t in toks[1:])
    if not _valid_multiplicity(text):
        raise LineError(
            "E_UNEXPECTED_TOKEN",
            f"多重度は 1・*・n・0..1・1..* の形: {text!r}",
            toks[0],
        )


def _valid_multiplicity(text: str) -> bool:
    def card(x: str) -> bool:
        return x in ("*", "n") or (x.isascii() and x.isdigit())

    parts = text.split("..")
    if len(parts) == 1:
        return card(parts[0])
    return len(parts) == 2 and card(parts[0]) and card(parts[1]) and parts[0] != "*"


def _member(ctx: Ctx, cur: Cursor, st: _State) -> None:
    cls = st.current
    assert cls is not None
    head = cur.peek()
    assert head is not None
    if head.text == "}" and head.kind == "PUNCT":
        cur.take("ir")
        st.current = None
        return
    if head.text == "<<":
        cur.take("ir")
        kind = cur.take(
            "ir", kind="NAME", what="enumeration か module", code="E_UNSUPPORTED_SYNTAX"
        )
        stereotype = _STEREOTYPES.get(kind.text)
        if stereotype is None:
            raise LineError("E_UNSUPPORTED_SYNTAX", "ステレオタイプは enumeration か module", kind)
        cur.take("ir", text=">>", what="`>>`")
        if cls.stereotype not in ("class", stereotype):
            raise LineError(
                "E_UNSUPPORTED_SYNTAX", f"クラス {cls.name} のステレオタイプは 1 つだけ", head
            )
        cls.stereotype = stereotype
        return
    if head.kind == "PUNCT" and head.text in _VISIBILITY:
        cur.take("ignored")
    first = cur.take_name("ir", "メンバー")
    if cur.at("("):
        cur.take("ir")
        params: list[str] = []
        if not cur.at(")"):
            params.append(cur.take_name("ir", "引数名").text)
            while cur.at(","):
                cur.take("ir")
                params.append(cur.take_name("ir", "引数名").text)
        cur.take("ir", text=")", what="`)`")
        if len(set(params)) != len(params):
            raise LineError("E_DUPLICATE_ID", f"操作 {first.text} の引数名が重複", first)
        register_id(st.member_ids, first)
        cls.operations.append(ClassOperation(name=first.text, params=params))
        return
    if cur.at_end():
        if cls.stereotype == "module":
            raise LineError("E_MODULE_MEMBER", f"モジュールは列挙値 {first.text} を持てない", first)
        if cls.stereotype != "enumeration":
            raise LineError(
                "E_UNEXPECTED_TOKEN",
                f"{first.text} は型の無い属性（列挙値は <<enumeration>> の後に書く）",
                first,
            )
        register_id(st.member_ids, first)
        cls.enum_values.append(first.text)
        return
    name = cur.take_name("ir", "属性名")
    if cls.stereotype == "module":
        raise LineError("E_MODULE_MEMBER", f"モジュールは属性 {name.text} を持てない", first)
    if "." in first.text:
        raise LineError("E_UNKNOWN_TYPE", f"属性の型 {first.text} は読めない", first)
    register_id(st.member_ids, name)
    cls.attributes.append(RawAttribute(name=name.text, type_name=first.text, span=ctx.at(first)))


__all__ = ["parse_class_diagram"]
