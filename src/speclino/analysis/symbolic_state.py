"""記号的状態と、Python の式から Expr への変換（PoC 9.1 の 5）。

ドメイン変数の現在値は Expr で持つ（初期値は before.<var> への参照）。状態更新後に評価した条件は
更新後の値で組み立てる（PoC 9.2）。コメント・名前は根拠にしない。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from speclino.analysis.static_types import StaticTypes
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
    analyzed_call: bool = False
    """path_analyzer._assign が呼出し（_call）を通して副作用（イベント・展開）を記録した後に
    結果のローカルへ付ける印。code の値では判別しない（式の中の未解析の呼出し
    ``to_expr`` の ``ast.Call()`` 分岐も同じ "unresolved_call" を使うため、code だけでは
    「解析済みの呼出しの戻り値」と「解析していない呼出し」を区別できない）。"""


@dataclass(frozen=True)
class RootAlias:
    """入口引数などの「オブジェクトの根」への別名（属性参照の起点）。"""

    root: str


LocalValue = Expr | Unresolved | RootAlias


class AmbiguousBinding(ValueError):
    """1 つの実装の参照に、検査の domain の変数が複数対応している。"""


@dataclass
class SymState:
    domain: DomainSpec
    binding: BehaviorBinding
    values: dict[str, Expr] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 対応（bindings の variables）のうち、この検査の domain が宣言する変数だけを使う。
        # 仕様ごとに domain が違う（旧形式の `has_findings` と図の `triage.has_findings` を
        # 同じ実装 `t.has_findings` に対応づける）ため。domain に無い変数の対応は無いものとして
        # 扱い、その参照は未解決（経路は UNKNOWN）になる。1 つの実装に domain の変数が 2 つ
        # 対応すると経路条件とガードが別の変数になり得るので、解析できないとする
        self.impl_to_var: dict[str, str] = {}
        # 契約済みの純粋な呼出し結果を名前付き変数として扱う
        # （例 self.permissions.allows → permitted）
        self.pure_call_vars: dict[str, str] = {}
        for var, impl in self.binding.variables.items():
            if var not in self.domain.variables:
                continue
            table = self.pure_call_vars if impl.startswith("pure_call:") else self.impl_to_var
            key = impl.removeprefix("pure_call:")
            if table.get(key, var) != var:
                raise AmbiguousBinding(
                    f"実装 {impl} に domain の変数 {table[key]} と {var} が対応している"
                )
            table[key] = var
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
    """関数 1 つ分の名前解決環境。types はローカル名 → 宣言型（static_types）。"""

    module: ModuleInfo
    locals: dict[str, LocalValue] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)

    def bind(self, name: str, value: LocalValue, ty: str | None = None) -> None:
        """ローカルへの代入。型は代入した値のものだけを持つ（再代入で前の型を捨てる）。"""
        self.locals[name] = value
        if ty is None:
            self.types.pop(name, None)
        else:
            self.types[name] = ty

    def copy(self) -> Frame:
        return Frame(self.module, dict(self.locals), dict(self.types))


class ExprBuilder:
    def __init__(self, index: RepoIndex, domain: DomainSpec) -> None:
        self.index = index
        self.domain = domain
        self.env = TypeEnv(domain)
        self.types = StaticTypes(index)

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

    def body_target(
        self, call: ast.Call, frame: Frame, cands: list[str], *, typed_ok: bool
    ) -> tuple[str, LocalValue | None] | None:
        """呼出し先の本体を展開・走査するときの (関数, self に束縛する値)。

        名前で解決した関数（`callee_qualname`）は引数をそのまま合わせる（self は無いか、
        `Cls.m(obj, ...)` の形で実引数にある）。宣言型で解決したメソッドは typed_ok のときだけで、
        self には受け手の値（入口の引数を根に持つ属性チェーンなら別名、ほかは未解決）を束縛する。
        どちらでもなければ None（本体を見られない）。
        """
        qual = self.callee_qualname(call.func, frame)
        if qual is not None and cands and cands[0] == qual and self.index.is_function(qual):
            return qual, None
        chain = self.attribute_chain(call.func)
        typed = cands[-1] if cands and cands[-1] != qual else None
        if not typed_ok or typed is None or chain is None or not self.index.is_function(typed):
            return None
        root = frame.locals.get(chain[0])
        receiver: LocalValue
        if isinstance(root, RootAlias):
            receiver = RootAlias(".".join([root.root, *chain[1:-1]]))
        else:
            receiver = Unresolved(
                "unresolved_attribute",
                f"{'.'.join(chain[:-1])} は入口の引数を根に持たない",
                call.lineno,
            )
        return typed, receiver

    def callee_candidates(self, func: ast.expr, frame: Frame) -> list[str]:
        """呼出し先の候補: `callee_qualname` と、宣言型で解決した完全修飾名（あれば）。

        bindings の symbol はどちらの書き方でもよい（`self.registry.execute` と
        `pkg.mod.ToolRegistry.execute`）。照合はこの候補のどれかとの一致で行う。
        """
        out: list[str] = []
        qual = self.callee_qualname(func, frame)
        if qual is not None:
            out.append(qual)
        chain = self.attribute_chain(func)
        if chain is not None and chain[0] in frame.locals:
            typed = self.types.typed_callee(chain, frame.types)
            if typed is not None and typed not in out:
                out.append(typed)
        return out

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
