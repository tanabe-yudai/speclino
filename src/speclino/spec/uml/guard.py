"""ガード → `Expr`（grammar.md 3 章）。手書きの再帰下降。式を eval しない。"""

from __future__ import annotations

from speclino.domain.expr import (
    ARITH_OPS,
    BOOL_TYPE,
    COMPARISON_OPS,
    INT_TYPE,
    Binary,
    Const,
    Expr,
    Nary,
    Not,
    Ref,
)
from speclino.spec.uml._parse import Cursor, LineError
from speclino.spec.uml.limits import MAX_EXPR_DEPTH
from speclino.spec.uml.types import Token

_CMP = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_ARITH = {"+": "add", "-": "sub"}
_SYMBOL = {v: k for k, v in {**_CMP, **_ARITH}.items()}
_NAMESPACES = ("before.", "after.", "outcome.")
_KEYWORDS = frozenset({"and", "or", "not", "true", "false"})


def parse_guard(cur: Cursor) -> Expr:
    """カーソルの位置からガードを 1 つ読む。

    読み終わりは `]`・`|`・行末のどれか（呼び出し側が確かめる）。
    """
    start = cur.peek()
    if start is None or start.text in ("]", "|"):
        raise LineError("E_GUARD_SYNTAX", "ガードが空", start or cur.last())
    e = _or(cur, 0)
    if not _bool_shaped(e):
        raise LineError("E_GUARD_TYPE", "ガードは真偽値の式でなければならない", start)
    return e


def _or(cur: Cursor, depth: int) -> Expr:
    args = [_and(cur, depth)]
    while cur.at("or"):
        cur.take("ir")
        args.append(_and(cur, depth))
    return _nary("or", args, cur)


def _and(cur: Cursor, depth: int) -> Expr:
    args = [_not(cur, depth)]
    while cur.at("and"):
        cur.take("ir")
        args.append(_not(cur, depth))
    return _nary("and", args, cur)


def _nary(op: str, args: list[Expr], cur: Cursor) -> Expr:
    if len(args) == 1:
        return args[0]
    for a in args:
        if not _bool_shaped(a):
            raise LineError("E_GUARD_TYPE", f"{op} の項は真偽値の式でなければならない", cur.last())
    return Nary(op=op, args=args)


def _not(cur: Cursor, depth: int) -> Expr:
    if cur.at("not"):
        tok = cur.take("ir")
        _check_depth(depth + 1, tok)
        arg = _not(cur, depth + 1)
        if not _bool_shaped(arg):
            raise LineError("E_GUARD_TYPE", "not の項は真偽値の式でなければならない", tok)
        return Not(op="not", arg=arg)
    return _cmp(cur, depth)


def _cmp(cur: Cursor, depth: int) -> Expr:
    left = _arith(cur, depth)
    t = cur.peek()
    if t is not None and t.kind == "PUNCT" and t.text in _CMP:
        _check_depth(depth + 1, t)
        cur.take("ir")
        right = _arith(cur, depth + 1)
        nxt = cur.peek()
        if nxt is not None and nxt.kind == "PUNCT" and nxt.text in _CMP:
            raise LineError("E_GUARD_SYNTAX", "比較は連鎖できない（and で分ける）", nxt)
        return Binary(op=_CMP[t.text], left=left, right=right)
    return left


def _arith(cur: Cursor, depth: int) -> Expr:
    left = _atom(cur, depth)
    while True:
        t = cur.peek()
        if t is None or t.kind != "PUNCT" or t.text not in _ARITH:
            return left
        depth += 1
        _check_depth(depth, t)
        cur.take("ir")
        right = _atom(cur, depth)
        left = Binary(op=_ARITH[t.text], left=left, right=right)


def _atom(cur: Cursor, depth: int) -> Expr:
    t = cur.peek()
    if t is None:
        raise LineError("E_GUARD_SYNTAX", "ガードの式が途中で終わった", cur.last())
    if t.text == "(" and t.kind == "PUNCT":
        cur.take("ir")
        _check_depth(depth + 1, t)
        inner = _or(cur, depth + 1)
        cur.take("ir", text=")", what="`)`", code="E_GUARD_SYNTAX")
        return inner
    if t.text == "-" and t.kind == "PUNCT":
        nxt = cur.peek(1)
        if nxt is not None and nxt.kind == "INT":
            cur.take("ir")
            cur.take("ir")
            return Const(op="const", type=INT_TYPE, value=-int(nxt.text))
    if t.kind == "INT":
        cur.take("ir")
        return Const(op="const", type=INT_TYPE, value=int(t.text))
    if t.kind == "NAME":
        if t.text in ("true", "false"):
            cur.take("ir")
            return Const(op="const", type=BOOL_TYPE, value=t.text == "true")
        if t.text.startswith(_NAMESPACES):
            cur.take("ir")
            return Ref(op="ref", path=t.text)
        if t.text not in _KEYWORDS and t.text.count(".") == 1:
            cur.take("ir")
            type_name, value = t.text.split(".")
            return Const(op="const", type=type_name, value=value)
        raise LineError(
            "E_GUARD_SYNTAX",
            f"{t.text!r} は読めない（参照は before./after./outcome. が必須、列挙値は 型名.値）",
            t,
        )
    if t.kind == "TEXT":
        raise LineError("E_GUARD_SYNTAX", f"自然言語のガードは書けない: {t.text!r}", t)
    raise LineError("E_GUARD_SYNTAX", f"ガードに {t.text!r} は書けない", t)


def _check_depth(depth: int, tok: Token) -> None:
    if depth > MAX_EXPR_DEPTH:
        raise LineError("E_NESTING_TOO_DEEP", f"ガードの式の深さは {MAX_EXPR_DEPTH} まで", tok)


def _bool_shaped(e: Expr) -> bool:
    """型環境なしで分かる範囲の「真偽値らしさ」。参照は型が分からないので通す。"""
    if isinstance(e, Const):
        return e.type == BOOL_TYPE
    if isinstance(e, Binary):
        return e.op in COMPARISON_OPS
    return isinstance(e, (Ref, Not, Nary))


def _atomic(e: Expr) -> bool:
    return isinstance(e, (Ref, Const))


def _wrap(e: Expr) -> str:
    s = render_expr(e)
    return s if _atomic(e) else f"({s})"


def render_expr(e: Expr) -> str:
    """正規形の表記。`parse_guard(render_expr(e)) == e` を保つ。

    2 項演算の左右と Nary の要素は、参照・定数以外を括弧で囲む。
    """
    if isinstance(e, Ref):
        return e.path
    if isinstance(e, Const):
        if e.type == BOOL_TYPE:
            return "true" if e.value is True else "false"
        if e.type == INT_TYPE:
            return str(e.value)
        return f"{e.type}.{e.value}"
    if isinstance(e, Not):
        return f"not {_wrap(e.arg)}"
    if isinstance(e, Binary):
        assert e.op in COMPARISON_OPS | ARITH_OPS
        return f"{_wrap(e.left)} {_SYMBOL[e.op]} {_wrap(e.right)}"
    return f" {e.op} ".join(_wrap(a) for a in e.args)


__all__ = ["parse_guard", "render_expr"]
