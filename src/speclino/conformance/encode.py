"""Expr を Z3 の式へ写す。IR の式を Python の eval で実行しない。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import z3

from speclino.conformance.typing import TypeEnv, infer_type
from speclino.domain.expr import BOOL_TYPE, INT_TYPE, Binary, Const, Expr, Nary, Not, Ref


@dataclass
class Z3Context:
    """ソートと変数定数のキャッシュ。1 回の検査（Rule × Path）ごとに作る必要はなく、
    同じ TypeEnv を共有する範囲で使い回せる。"""

    env: TypeEnv
    z3ctx: Any = field(default_factory=z3.Context)  # 列挙ソート名は Z3 の Context 単位で一意
    sorts: dict[str, Any] = field(default_factory=dict)
    enum_consts: dict[str, dict[str, Any]] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)

    def sort(self, type_name: str) -> Any:
        if type_name in self.sorts:
            return self.sorts[type_name]
        if type_name == BOOL_TYPE:
            s = z3.BoolSort(self.z3ctx)
        elif type_name == INT_TYPE:
            s = z3.IntSort(self.z3ctx)
        elif self.env.is_enum(type_name):
            values = self.env.enum_values(type_name) or []
            s, consts = z3.EnumSort(type_name, values, ctx=self.z3ctx)
            self.enum_consts[type_name] = dict(zip(values, consts, strict=True))
        elif self.env.is_opaque(type_name):
            s = z3.DeclareSort(type_name, self.z3ctx)
        else:
            raise ValueError(f"未定義の型: {type_name}")
        self.sorts[type_name] = s
        return s

    def variable(self, path: str, type_name: str) -> Any:
        """名前空間つきの変数（before.order.status 等）を Z3 定数にする。"""
        if path not in self.variables:
            self.variables[path] = z3.Const(path, self.sort(type_name))
        return self.variables[path]

    def const(self, c: Const) -> Any:
        if c.type == BOOL_TYPE:
            return z3.BoolVal(bool(c.value), self.z3ctx)
        if c.type == INT_TYPE:
            return z3.IntVal(int(c.value), self.z3ctx)
        if self.env.is_enum(c.type):
            self.sort(c.type)
            return self.enum_consts[c.type][str(c.value)]
        if self.env.is_opaque(c.type):
            # opaque の定数は同一性だけ持つ名前付き定数
            return z3.Const(f"{c.type}::{c.value}", self.sort(c.type))
        raise ValueError(f"未定義の型: {c.type}")

    def encode(self, expr: Expr) -> Any:
        match expr:
            case Ref():
                return self.variable(expr.path, self.env.type_of_ref(expr))
            case Const():
                return self.const(expr)
            case Not():
                return z3.Not(self.encode(expr.arg))
            case Binary():
                left, right = self.encode(expr.left), self.encode(expr.right)
                match expr.op:
                    case "eq":
                        return left == right
                    case "ne":
                        return left != right
                    case "lt":
                        return left < right
                    case "le":
                        return left <= right
                    case "gt":
                        return left > right
                    case "ge":
                        return left >= right
                    case "add":
                        return left + right
                    case "sub":
                        return left - right
            case Nary():
                parts = [self.encode(a) for a in expr.args]
                return z3.And(*parts) if expr.op == "and" else z3.Or(*parts)
        raise ValueError(f"未知の式: {expr!r}")

    def solver(self, timeout_ms: int) -> Any:
        s = z3.Solver(ctx=self.z3ctx)
        s.set("timeout", timeout_ms)
        return s

    def true(self) -> Any:
        return z3.BoolVal(True, self.z3ctx)

    def encode_bool(self, expr: Expr) -> Any:
        if infer_type(self.env, expr) != BOOL_TYPE:
            raise ValueError("boolean 式が必要")
        return self.encode(expr)

    def value_of(self, model: Any, path: str) -> bool | int | str | None:
        """モデルから変数の値を Python 値にして返す（列挙は名前、opaque は識別子文字列）。"""
        v = self.variables.get(path)
        if v is None:
            return None
        val = model.eval(v, model_completion=True)
        if z3.is_bool(val):
            return bool(z3.is_true(val))
        if z3.is_int_value(val):
            return int(val.as_long())
        return str(val)
