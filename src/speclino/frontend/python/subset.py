"""対応構文の判定（PoC 3.2 / 3.3）。対象外は黙って落とさず理由と位置を返す。"""

from __future__ import annotations

import ast
from dataclasses import dataclass

SUPPORTED_STMTS: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.Return,
    ast.Raise,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.Pass,
)
SUPPORTED_COMPARE_OPS: tuple[type[ast.cmpop], ...] = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)
SUPPORTED_BINOPS: tuple[type[ast.operator], ...] = (ast.Add, ast.Sub)


@dataclass(frozen=True)
class Unsupported:
    detail: str
    lineno: int


def unsupported_stmt(stmt: ast.stmt) -> Unsupported | None:
    if isinstance(stmt, SUPPORTED_STMTS):
        return None
    return Unsupported(f"{type(stmt).__name__} は対応外の構文", stmt.lineno)


def unsupported_expr(expr: ast.expr) -> Unsupported | None:
    """式の骨格が対応範囲か。名前解決の可否は analyzer が判断する。"""
    match expr:
        case ast.Name() | ast.Constant():
            return None
        case ast.Attribute():
            return unsupported_expr(expr.value)
        case ast.BoolOp():
            for v in expr.values:
                if (u := unsupported_expr(v)) is not None:
                    return u
            return None
        case ast.UnaryOp():
            if not isinstance(expr.op, ast.Not):
                return Unsupported(f"単項演算 {type(expr.op).__name__} は対応外", expr.lineno)
            return unsupported_expr(expr.operand)
        case ast.Compare():
            if len(expr.ops) != 1 or not isinstance(expr.ops[0], SUPPORTED_COMPARE_OPS):
                return Unsupported("連鎖比較または対応外の比較演算子", expr.lineno)
            return unsupported_expr(expr.left) or unsupported_expr(expr.comparators[0])
        case ast.BinOp():
            if not isinstance(expr.op, SUPPORTED_BINOPS):
                return Unsupported(f"二項演算 {type(expr.op).__name__} は対応外", expr.lineno)
            return unsupported_expr(expr.left) or unsupported_expr(expr.right)
        case ast.Call():
            if any(isinstance(a, ast.Starred) for a in expr.args) or any(
                k.arg is None for k in expr.keywords
            ):
                return Unsupported("可変長引数の展開は対応外", expr.lineno)
            for a in [*expr.args, *(k.value for k in expr.keywords)]:
                if (u := unsupported_expr(a)) is not None:
                    return u
            return unsupported_expr(expr.func)
        case _:
            return Unsupported(f"{type(expr).__name__} は対応外の式", expr.lineno)


def function_decorators(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [ast.unparse(d) for d in fn.decorator_list]
