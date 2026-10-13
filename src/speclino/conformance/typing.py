"""Expr の型推論と型検査。spec_validator と conformance_engine が共有する。

型は boolean / integer / 列挙型 / opaque ID の 4 種。outcome 名前空間には
モデル化した終了結果の組込み変数だけがある。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from speclino.domain.domain_spec import DomainSpec
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

OUTCOME_KIND_ENUM = "OutcomeKind"
OUTCOME_KIND_VALUES = ("http_response", "return", "exception", "unresolved")
# outcome 名前空間の組込み変数（名前空間なしの名前 → 型）
OUTCOME_VARIABLES: dict[str, str] = {
    "status": INT_TYPE,
    "kind": OUTCOME_KIND_ENUM,
}


class IRTypeError(ValueError):
    """型が合わない、または未定義の参照。"""


@dataclass(frozen=True)
class TypeEnv:
    domain: DomainSpec
    extra_variables: dict[str, str] = field(default_factory=dict)

    def enum_values(self, type_name: str) -> list[str] | None:
        if type_name == OUTCOME_KIND_ENUM:
            return list(OUTCOME_KIND_VALUES)
        return self.domain.enums.get(type_name)

    def is_enum(self, type_name: str) -> bool:
        return type_name == OUTCOME_KIND_ENUM or self.domain.is_enum(type_name)

    def is_opaque(self, type_name: str) -> bool:
        return self.domain.is_opaque(type_name)

    def type_of_ref(self, ref: Ref) -> str:
        if ref.namespace == "outcome":
            ty = OUTCOME_VARIABLES.get(ref.variable)
            if ty is None:
                raise IRTypeError(f"outcome の変数が未定義: {ref.path}")
            return ty
        ty = self.domain.type_of(ref.variable) or self.extra_variables.get(ref.variable)
        if ty is None:
            raise IRTypeError(f"変数が domain に未定義: {ref.path}")
        return ty


def check_const(env: TypeEnv, c: Const) -> str:
    """Const の値が宣言型に合うか検査し、型名を返す。"""
    ty, v = c.type, c.value
    if ty == BOOL_TYPE:
        if not isinstance(v, bool):
            raise IRTypeError(f"boolean 定数の値が不正: {v!r}")
    elif ty == INT_TYPE:
        if isinstance(v, bool) or not isinstance(v, int):
            raise IRTypeError(f"integer 定数の値が不正: {v!r}")
    elif env.is_enum(ty):
        values = env.enum_values(ty) or []
        if not isinstance(v, str) or v not in values:
            raise IRTypeError(f"列挙 {ty} に無い値: {v!r}")
    elif env.is_opaque(ty):
        if not isinstance(v, str):
            raise IRTypeError(f"opaque {ty} の定数は文字列: {v!r}")
    else:
        raise IRTypeError(f"未定義の型: {ty!r}")
    return ty


def infer_type(env: TypeEnv, expr: Expr) -> str:
    """式の型を返す。合わなければ IRTypeError。"""
    match expr:
        case Ref():
            return env.type_of_ref(expr)
        case Const():
            return check_const(env, expr)
        case Not():
            inner = infer_type(env, expr.arg)
            if inner != BOOL_TYPE:
                raise IRTypeError(f"not の引数は boolean: {inner}")
            return BOOL_TYPE
        case Binary():
            lt, rt = infer_type(env, expr.left), infer_type(env, expr.right)
            if expr.op in COMPARISON_OPS:
                if lt != rt:
                    raise IRTypeError(f"{expr.op} の両辺の型が違う: {lt} と {rt}")
                if expr.op in ("lt", "le", "gt", "ge") and lt != INT_TYPE:
                    raise IRTypeError(f"{expr.op} は integer にのみ使える: {lt}")
                return BOOL_TYPE
            if expr.op in ARITH_OPS:
                if lt != INT_TYPE or rt != INT_TYPE:
                    raise IRTypeError(f"{expr.op} は integer にのみ使える: {lt}, {rt}")
                return INT_TYPE
        case Nary():
            for a in expr.args:
                t = infer_type(env, a)
                if t != BOOL_TYPE:
                    raise IRTypeError(f"{expr.op} の引数は boolean: {t}")
            return BOOL_TYPE
    raise IRTypeError(f"未知の式: {expr!r}")


def require_boolean(env: TypeEnv, expr: Expr, what: str) -> None:
    t = infer_type(env, expr)
    if t != BOOL_TYPE:
        raise IRTypeError(f"{what} は boolean でなければならない: {t}")
