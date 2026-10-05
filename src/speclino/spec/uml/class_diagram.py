"""クラス図（grammar.md 4 章）→ クラスの語彙。型名の解決は compile が図をまたいで行う。"""

from __future__ import annotations

from speclino.domain.uml import ClassOperation
from speclino.spec.uml._parse import DIRECTIONS, Ctx, Cursor, LineError, register_id
from speclino.spec.uml.types import DiagramResult, RawAttribute, RawClass, Token

_VISIBILITY = frozenset({"+", "-", "#", "~"})


class _State:
    def __init__(self) -> None:
        self.current: RawClass | None = None
        self.current_tok: Token | None = None
        self.class_ids: dict[str, Token] = {}
        self.member_ids: dict[str, Token] = {}


def parse_class_diagram(ctx: Ctx, lines: list[list[Token]], result: DiagramResult) -> None:
    st = _State()
    for toks in lines:
        ctx.run(toks, lambda cur: _line(ctx, cur, st, result))
    if st.current is not None:
        ctx.error("E_UNCLOSED_BLOCK", f"class {st.current.name} の `}}` が無い", st.current_tok)
    for cls in result.classes:
        _check_class(ctx, cls, st.class_ids[cls.name])


def _check_class(ctx: Ctx, cls: RawClass, tok: Token) -> None:
    if cls.enumeration:
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
    if head.text != "class" or head.kind != "NAME":
        raise LineError(
            "E_UNSUPPORTED_SYNTAX",
            f"クラス図で対応しない構文: {head.text!r}（関係線・注記・`Class : member` は書けない）",
            head,
        )
    cur.take("ir")
    name = cur.take_name("ir", "クラス名")
    register_id(st.class_ids, name)
    cls = RawClass(
        name=name.text,
        enumeration=False,
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
        cur.take("ir", text="enumeration", what="enumeration", code="E_UNSUPPORTED_SYNTAX")
        cur.take("ir", text=">>", what="`>>`")
        cls.enumeration = True
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
        if not cls.enumeration:
            raise LineError(
                "E_UNEXPECTED_TOKEN",
                f"{first.text} は型の無い属性（列挙値は <<enumeration>> の後に書く）",
                first,
            )
        register_id(st.member_ids, first)
        cls.enum_values.append(first.text)
        return
    name = cur.take_name("ir", "属性名")
    if "." in first.text:
        raise LineError("E_UNKNOWN_TYPE", f"属性の型 {first.text} は読めない", first)
    register_id(st.member_ids, name)
    cls.attributes.append(RawAttribute(name=name.text, type_name=first.text, span=ctx.at(first)))


__all__ = ["parse_class_diagram"]
