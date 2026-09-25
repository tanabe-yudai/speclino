"""記号的状態と、Python の式から Expr への変換（PoC 9.1 の 5）。

ドメイン変数の現在値は Expr で持つ（初期値は before.<var> への参照）。状態更新後に評価した条件は
更新後の値で組み立てる（PoC 9.2）。コメント・名前は根拠にしない。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from speclino.conformance.typing import IRTypeError, TypeEnv, infer_type
from speclino.domain.bindings import BehaviorBinding
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import (
    BOOL_TYPE,
    INT_TYPE,
    Binary,
    Const,
    Expr,
    Nary,
    Not,
    const,
    ref,
)
from speclino.frontend.python.index import ModuleInfo, RepoIndex, attribute_chain


@dataclass(frozen=True)
class Unresolved:
    """Expr に変換できなかった理由。"""

    code: str  # unresolved_attribute | unsupported_syntax | unresolved_call
    detail: str
    lineno: int


@dataclass(frozen=True)
class RootAlias:
    """入口引数などの「オブジェクトの根」への別名（属性参照の起点）。"""

    root: str


LocalValue = Expr | Unresolved | RootAlias


@dataclass
class SymState:
    domain: DomainSpec
    binding: BehaviorBinding
    values: dict[str, Expr] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.impl_to_var = {
            impl: var
            for var, impl in self.binding.variables.items()
            if not impl.startswith("pure_call:")
        }
        # 契約済みの純粋な呼出し結果を名前付き変数として扱う
        # （例 self.permissions.allows → permitted）
        self.pure_call_vars = {
            impl.removeprefix("pure_call:"): var
            for var, impl in self.binding.variables.items()
            if impl.startswith("pure_call:")
        }
        for var in self.domain.variables:
            self.values.setdefault(var, ref(f"before.{var}"))

    def var_of_impl(self, impl_ref: str) -> str | None:
        return self.impl_to_var.get(impl_ref)

    def value(self, var: str) -> Expr:
        return self.values[var]

    def assign(self, var: str, value: Expr) -> None:
        self.values[var] = value

    def copy(self) -> SymState:
        return SymState(self.domain, self.binding, dict(self.values))


@dataclass
class Frame:
    """関数 1 つ分の名前解決環境。"""

    module: ModuleInfo
    locals: dict[str, LocalValue] = field(default_factory=dict)

    def copy(self) -> Frame:
        return Frame(self.module, dict(self.locals))


class ExprBuilder:
    def __init__(self, index: RepoIndex, domain: DomainSpec) -> None:
        self.index = index
        self.domain = domain
        self.env = TypeEnv(domain)

    # ---------------------------------------------------------------- helpers
    def attribute_chain(self, node: ast.expr) -> list[str] | None:
        return attribute_chain(node)

    def callee_qualname(self, func: ast.expr, frame: Frame) -> str | None:
        """呼出し先のシンボル。モジュール名で解決できれば完全修飾名、入口引数（self 等）を根に
        持つメソッド呼出しは別名を展開した属性チェーン（例 self.registry.execute）を返す。
        後者は bindings.operations の symbol と文字列で照合する。"""
        chain = self.attribute_chain(func)
        if not chain:
            return None
        if chain[0] in frame.locals:
            root = frame.locals[chain[0]]
            if isinstance(root, RootAlias) and len(chain) > 1:
                return ".".join([root.root, *chain[1:]])
            return None
        head = frame.module.resolve_name(chain[0])
        return ".".join([head, *chain[1:]])

    def type_of(self, expr: Expr) -> str | None:
        try:
            return infer_type(self.env, expr)
        except IRTypeError:
            return None

    # ---------------------------------------------------------------- convert
    def to_expr(
        self, node: ast.expr, frame: Frame, state: SymState, expected: str | None = None
    ) -> Expr | Unresolved:
        match node:
            case ast.Constant():
                return self._constant(node, expected)
            case ast.Name():
                return self._name(node, frame, state)
            case ast.Attribute():
                return self._attribute(node, frame, state)
            case ast.BoolOp():
                args: list[Expr] = []
                for v in node.values:
                    e = self.to_expr(v, frame, state, BOOL_TYPE)
                    if isinstance(e, Unresolved):
                        return e
                    args.append(e)
                return Nary(op="and" if isinstance(node.op, ast.And) else "or", args=args)
            case ast.UnaryOp(op=ast.Not()):
                inner = self.to_expr(node.operand, frame, state, BOOL_TYPE)
                return inner if isinstance(inner, Unresolved) else Not(op="not", arg=inner)
            case ast.Compare() if len(node.ops) == 1:
                return self._compare(node, frame, state)
            case ast.BinOp() if isinstance(node.op, ast.Add | ast.Sub):
                left = self.to_expr(node.left, frame, state, INT_TYPE)
                if isinstance(left, Unresolved):
                    return left
                right = self.to_expr(node.right, frame, state, INT_TYPE)
                if isinstance(right, Unresolved):
                    return right
                return Binary(
                    op="add" if isinstance(node.op, ast.Add) else "sub", left=left, right=right
                )
            case ast.Call():
                return Unresolved(
                    "unresolved_call",
                    f"呼出し結果を値として扱えない: {ast.unparse(node)}",
                    node.lineno,
                )
            case _:
                return Unresolved(
                    "unsupported_syntax", f"{type(node).__name__} は対応外の式", node.lineno
                )

    def _constant(self, node: ast.Constant, expected: str | None) -> Expr | Unresolved:
        v = node.value
        if isinstance(v, bool):
            return const(BOOL_TYPE, v)
        if isinstance(v, int):
            return const(INT_TYPE, v)
        if isinstance(v, str):
            if expected and (self.env.is_enum(expected) or self.env.is_opaque(expected)):
                if self.env.is_enum(expected) and v not in (self.env.enum_values(expected) or []):
                    return Unresolved(
                        "unresolved_attribute", f"列挙 {expected} に無い値 {v!r}", node.lineno
                    )
                return const(expected, v)
            return Unresolved(
                "unresolved_attribute", f"文字列定数 {v!r} の型を決められない", node.lineno
            )
        return Unresolved("unsupported_syntax", f"定数 {v!r} は対応外", node.lineno)

    def _name(self, node: ast.Name, frame: Frame, state: SymState) -> Expr | Unresolved:
        if node.id in frame.locals:
            val = frame.locals[node.id]
            if isinstance(val, RootAlias):
                var = state.var_of_impl(val.root)
                if var is None:
                    return Unresolved(
                        "unresolved_attribute", f"{node.id} はドメイン変数に対応しない", node.lineno
                    )
                return state.value(var)
            return val
        return Unresolved("unresolved_attribute", f"未定義の名前 {node.id}", node.lineno)

    def _attribute(self, node: ast.Attribute, frame: Frame, state: SymState) -> Expr | Unresolved:
        chain = self.attribute_chain(node)
        if chain is None:
            return Unresolved("unsupported_syntax", "属性参照の根が名前でない", node.lineno)
        head = chain[0]
        if head in frame.locals:
            val = frame.locals[head]
            if not isinstance(val, RootAlias):
                return Unresolved(
                    "unresolved_attribute", f"{'.'.join(chain)} の根が値の別名でない", node.lineno
                )
            impl = ".".join([val.root, *chain[1:]])
            var = state.var_of_impl(impl)
            if var is None:
                return Unresolved(
                    "unresolved_attribute", f"{impl} は bindings に無い属性", node.lineno
                )
            return state.value(var)
        # 列挙の定数（OrderStatus.PENDING）。列挙型名はクラス名と一致する前提
        qual = frame.module.resolve_name(head)
        values = self.index.enum_values(qual)
        if values is not None and len(chain) == 2 and chain[1] in values:
            enum_name = qual.rsplit(".", 1)[-1]
            if not self.env.is_enum(enum_name):
                return Unresolved(
                    "unresolved_attribute", f"列挙 {enum_name} が domain に無い", node.lineno
                )
            return const(enum_name, chain[1])
        return Unresolved("unresolved_attribute", f"{'.'.join(chain)} を解決できない", node.lineno)

    def _compare(self, node: ast.Compare, frame: Frame, state: SymState) -> Expr | Unresolved:
        op_map: dict[type[ast.cmpop], str] = {
            ast.Eq: "eq",
            ast.NotEq: "ne",
            ast.Lt: "lt",
            ast.LtE: "le",
            ast.Gt: "gt",
            ast.GtE: "ge",
        }
        op = op_map.get(type(node.ops[0]))
        if op is None:
            return Unresolved("unsupported_syntax", "対応外の比較演算子", node.lineno)
        left_node, right_node = node.left, node.comparators[0]
        # 定数側の型を、もう一方の型から決める
        if isinstance(left_node, ast.Constant) and not isinstance(right_node, ast.Constant):
            right = self.to_expr(right_node, frame, state)
            if isinstance(right, Unresolved):
                return right
            left = self.to_expr(left_node, frame, state, self.type_of(right))
            if isinstance(left, Unresolved):
                return left
        else:
            left = self.to_expr(left_node, frame, state)
            if isinstance(left, Unresolved):
                return left
            right = self.to_expr(right_node, frame, state, self.type_of(left))
            if isinstance(right, Unresolved):
                return right
        return Binary(op=op, left=left, right=right)  # type: ignore[arg-type,unused-ignore]


def guard_expr(conds: list[Expr]) -> Expr:
    if not conds:
        return Const(op="const", type=BOOL_TYPE, value=True)
    if len(conds) == 1:
        return conds[0]
    return Nary(op="and", args=list(conds))
