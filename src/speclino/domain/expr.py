"""型付き式木 Expr（PoC 6.5）。文字列の Python コードは持たず、eval しない。"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator

from speclino.domain.common import IRModel

# 変数参照は名前空間を明示する。before/after は入口と終了時の状態、outcome はモデル化した終了結果。
Namespace = Literal["before", "after", "outcome"]
_REF_RE = re.compile(r"^(before|after|outcome)\.[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")


class Ref(IRModel):
    op: Literal["ref"]
    path: str = Field(description="例: before.order.status")

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        if not _REF_RE.match(v):
            raise ValueError(f"ref.path は before|after|outcome で始まるドット区切り: {v!r}")
        return v

    @property
    def namespace(self) -> Namespace:
        ns = self.path.split(".", 1)[0]
        return ns  # type: ignore[return-value]

    @property
    def variable(self) -> str:
        """名前空間を除いた変数名（例: order.status）。"""
        return self.path.split(".", 1)[1]


class Const(IRModel):
    op: Literal["const"]
    type: str = Field(description="boolean / integer / 列挙型名 / opaque 型名")
    value: StrictBool | StrictInt | str


class Not(IRModel):
    op: Literal["not"]
    arg: Expr


class Binary(IRModel):
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "add", "sub"]
    left: Expr
    right: Expr


class Nary(IRModel):
    op: Literal["and", "or"]
    args: list[Expr] = Field(min_length=1, description="空配列は拒否する")


Expr = Annotated[Ref | Const | Not | Binary | Nary, Field(discriminator="op")]

Not.model_rebuild()
Binary.model_rebuild()
Nary.model_rebuild()

COMPARISON_OPS = frozenset({"eq", "ne", "lt", "le", "gt", "ge"})
ARITH_OPS = frozenset({"add", "sub"})
BOOL_TYPE = "boolean"
INT_TYPE = "integer"


def refs_in(expr: Expr) -> list[Ref]:
    """式に含まれる変数参照を出現順に返す。"""
    match expr:
        case Ref():
            return [expr]
        case Const():
            return []
        case Not():
            return refs_in(expr.arg)
        case Binary():
            return refs_in(expr.left) + refs_in(expr.right)
        case Nary():
            return [r for a in expr.args for r in refs_in(a)]
    raise TypeError(f"unknown expr: {expr!r}")


_BINARY_SYMBOLS = {
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "add": "+",
    "sub": "-",
}


def describe_expr(expr: Expr) -> str:
    """人が読む 1 行表記（レポート・反例・差分用。評価には使わない）。"""
    match expr:
        case Ref():
            return expr.path
        case Const():
            return str(expr.value)
        case Not():
            return f"not({describe_expr(expr.arg)})"
        case Binary():
            return (
                f"{describe_expr(expr.left)} {_BINARY_SYMBOLS[expr.op]} {describe_expr(expr.right)}"
            )
        case Nary():
            joiner = " and " if expr.op == "and" else " or "
            return "(" + joiner.join(describe_expr(a) for a in expr.args) + ")"
    raise TypeError(f"unknown expr: {expr!r}")


def ref(path: str) -> Ref:
    return Ref(op="ref", path=path)


def const(type_: str, value: bool | int | str) -> Const:
    return Const(op="const", type=type_, value=value)


def and_(*args: Expr) -> Nary:
    return Nary(op="and", args=list(args))


def or_(*args: Expr) -> Nary:
    return Nary(op="or", args=list(args))


def not_(arg: Expr) -> Not:
    return Not(op="not", arg=arg)


def eq(left: Expr, right: Expr) -> Binary:
    return Binary(op="eq", left=left, right=right)


def ne(left: Expr, right: Expr) -> Binary:
    return Binary(op="ne", left=left, right=right)
