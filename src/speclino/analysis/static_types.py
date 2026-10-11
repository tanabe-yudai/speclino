"""宣言された型（注釈・自明な構築）による呼出し先の解決（属性経由のメソッド呼出しの照合）。

`self.registry.execute(call)` の `self.registry` が `ToolRegistry` と静的に分かれば、呼出し先を
`pkg.mod.ToolRegistry.execute` と完全修飾名で表せる。図の操作名（`ToolRegistry.execute`）と
bindings の完全修飾のシンボルに名前で照合できるようにするためのもの。

型の根拠にするのは次だけ（それ以外・食い違い・曖昧なものは「型なし」= 従来どおり）:

- 引数の注釈（`registry: ToolRegistry`）。`X | None`・総称型・文字列以外の式は使わない
- メソッドの第 1 引数（`self`）は定義しているクラス
- インスタンス属性: クラス本体の注釈（`a: T`）と、メソッド内の `self.a = <注釈つき引数>` /
  `self.a: T = ...` / `self.a = T(...)`。代入がすべて同じ型に解決できるときだけ型を持つ。
  `__getattr__` / `__getattribute__` / `__setattr__` / `__slots__` を持つクラスの属性は型なし
- 呼出し結果の型: 呼出し先の戻り値の注釈（`-> T`、`async def` なら await した結果）
- 自明な構築 `T(...)`: 基底・デコレータ・メタクラスが無く、`__init__` が `self.x = <引数か定数>`
  だけ（または `__init__` が無い）のクラス。副作用が無いので呼出しイベントも未解決要因も作らない

型はクラス（リポジトリ内でモジュール直下に定義されたもの）の完全修飾名。宣言型で解決するので、
実行時のサブクラスの上書きは区別しない（呼出しは「宣言型の操作の呼出し」として扱う）。
対象を import しない（AST だけ）。
"""

from __future__ import annotations

import ast

from speclino.frontend.python.index import FuncDef, FunctionInfo, ModuleInfo, RepoIndex

_DYNAMIC_ATTRS = frozenset({"__getattr__", "__getattribute__", "__setattr__"})


class StaticTypes:
    def __init__(self, index: RepoIndex) -> None:
        self.index = index
        self._attr_cache: dict[tuple[str, str], str | None] = {}
        self._trivial_cache: dict[str, bool] = {}

    # ------------------------------------------------------------ annotations
    def class_of(self, node: ast.expr | None, module: ModuleInfo) -> str | None:
        """注釈の式がリポジトリ内のクラスを指せばその完全修飾名。"""
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
            return self.class_of(parsed, module)
        chain = _chain(node)
        if chain is None:
            return None
        qual = ".".join([module.resolve_name(chain[0]), *chain[1:]])
        return qual if self.index.class_def(qual) is not None else None

    def param_types(self, fn: FunctionInfo) -> dict[str, str]:
        """関数の引数名 → 型。メソッドなら第 1 引数は定義しているクラス。"""
        module = self.index.module(fn.module)
        if module is None:
            return {}
        out: dict[str, str] = {}
        args = fn.node.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        owner = fn.qualname.rpartition(".")[0]
        if self.index.class_def(owner) is not None and params and not fn.node.decorator_list:
            out[params[0].arg] = owner
            params = params[1:]
        for a in params:
            ty = self.class_of(a.annotation, module)
            if ty is not None:
                out[a.arg] = ty
        return out

    def return_type(self, qualname: str) -> str | None:
        try:
            fn = self.index.function(qualname)
        except LookupError:
            return None
        module = self.index.module(fn.module)
        return self.class_of(fn.node.returns, module) if module is not None else None

    # ------------------------------------------------------------ attributes
    def attr_type(self, cls: str, attr: str) -> str | None:
        key = (cls, attr)
        if key not in self._attr_cache:
            self._attr_cache[key] = self._attr_type(cls, attr)
        return self._attr_cache[key]

    def _attr_type(self, cls: str, attr: str) -> str | None:
        """`__init__` の `self.attr = <注釈つき引数> / T(...)` と `self.attr: T = ...` だけが根拠。

        ほかのメソッド・クラス本体・分解代入・累算・削除で書かれる属性は型なし（後から別の値に
        なり得る）。`__init__` の中の代入がすべて同じ型に解決できるときだけ型を持つ。
        """
        found = self.index.class_def(cls)
        if found is None:
            return None
        module, node = found
        types: set[str | None] = set()
        for s in node.body:
            if isinstance(s, FuncDef) and s.name in _DYNAMIC_ATTRS:
                return None
            if isinstance(s, ast.Assign | ast.AnnAssign):
                targets = s.targets if isinstance(s, ast.Assign) else [s.target]
                if any(isinstance(t, ast.Name) and t.id in (attr, "__slots__") for t in targets):
                    return None
            elif isinstance(s, FuncDef):
                writes = self._writes_in_method(s, attr, module)
                if s.name != "__init__" or s.decorator_list:
                    if writes:
                        return None
                    continue
                types |= writes
        if len(types) != 1:
            return None
        return next(iter(types))

    def _writes_in_method(self, fn: FuncDef, attr: str, module: ModuleInfo) -> set[str | None]:
        """メソッドの中の `self.attr` への書込みの型（根拠にできない書込みは None）。"""
        params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
        if not params:
            return set()
        me = params[0].arg
        annotated = {a.arg: a.annotation for a in params[1:]}
        out: set[str | None] = set()
        for sub in ast.walk(fn):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            annotation: ast.expr | None = None
            if isinstance(sub, ast.Assign):
                targets, value = list(sub.targets), sub.value
            elif isinstance(sub, ast.AnnAssign):
                targets, value, annotation = [sub.target], sub.value, sub.annotation
            elif isinstance(sub, ast.AugAssign | ast.Delete):
                targets = [sub.target] if isinstance(sub, ast.AugAssign) else list(sub.targets)
            for t in targets:
                for leaf in ast.walk(t):
                    if not (
                        isinstance(leaf, ast.Attribute)
                        and leaf.attr == attr
                        and isinstance(leaf.value, ast.Name)
                        and leaf.value.id == me
                    ):
                        continue
                    if leaf is not t or isinstance(sub, ast.AugAssign | ast.Delete):
                        out.add(None)  # 分解代入・累算・削除は型の根拠にしない
                    elif annotation is not None:
                        out.add(self.class_of(annotation, module))
                    elif isinstance(value, ast.Name) and value.id in annotated:
                        out.add(self.class_of(annotated[value.id], module))
                    elif isinstance(value, ast.Call):
                        out.add(self.class_of(value.func, module))
                    else:
                        out.add(None)
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id in ("setattr", "delattr")
            ):
                out.add(None)  # 動的な書込み
        return out

    # ------------------------------------------------------------ calls
    def method(self, cls: str, name: str) -> str | None:
        """クラスの本体に定義されたメソッドの完全修飾名（基底はたどらない）。"""
        qual = f"{cls}.{name}"
        return qual if self.index.is_function(qual) else None

    def typed_callee(self, chain: list[str], types: dict[str, str]) -> str | None:
        """`x.a.b.m` の x の型から属性をたどり、メソッド m の完全修飾名を返す。"""
        if len(chain) < 2 or chain[0] not in types:
            return None
        ty: str | None = types[chain[0]]
        for attr in chain[1:-1]:
            if ty is None:
                return None
            ty = self.attr_type(ty, attr)
        return self.method(ty, chain[-1]) if ty is not None else None

    def trivial_class(self, qualname: str) -> bool:
        """構築に副作用が無いと AST から言えるクラス（上の「自明な構築」）。"""
        if qualname not in self._trivial_cache:
            self._trivial_cache[qualname] = self._trivial(qualname)
        return self._trivial_cache[qualname]

    def _trivial(self, qualname: str) -> bool:
        found = self.index.class_def(qualname)
        if found is None:
            return False
        _, node = found
        if node.bases or node.keywords or node.decorator_list:
            return False
        init: FuncDef | None = None
        for s in node.body:
            if isinstance(s, FuncDef):
                if s.decorator_list:
                    return False  # property の setter など、属性の代入が呼出しになり得る
                if s.name in ("__new__", "__init_subclass__", "__post_init__", *_DYNAMIC_ATTRS):
                    return False
                if s.name == "__init__":
                    init = s
            elif isinstance(s, ast.Expr | ast.Pass):
                continue
            elif isinstance(s, ast.AnnAssign | ast.Assign):
                if s.value is not None and not isinstance(s.value, ast.Constant):
                    return False
            else:
                return False
        if init is None:
            return True
        if isinstance(init, ast.AsyncFunctionDef) or init.decorator_list:
            return False
        params = [a.arg for a in [*init.args.posonlyargs, *init.args.args, *init.args.kwonlyargs]]
        if not params:
            return False
        me, rest = params[0], set(params[1:])
        if init.args.vararg or init.args.kwarg:
            return False
        if any(d is not None and not isinstance(d, ast.Constant) for d in init.args.defaults):
            return False
        if any(d is not None and not isinstance(d, ast.Constant) for d in init.args.kw_defaults):
            return False
        for i, s in enumerate(init.body):
            if i == 0 and isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
                continue  # docstring
            target: ast.expr
            value: ast.expr | None
            if isinstance(s, ast.Assign) and len(s.targets) == 1:
                target, value = s.targets[0], s.value
            elif isinstance(s, ast.AnnAssign):
                target, value = s.target, s.value
            else:
                return False
            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == me
            ):
                return False
            if not (
                isinstance(value, ast.Constant)
                or (isinstance(value, ast.Name) and value.id in rest)
            ):
                return False
        return True


# 副作用の無い標準の関数（結果の値は追わない。呼出しを未解決にしないためだけに使う）。引数の
# `__str__` / `__len__` などの上書きは、属性の読み出しと同じく純粋とみなす（5.1.1）
PURE_FUNCTIONS = frozenset(
    {
        "len",
        "isinstance",
        "str",
        "int",
        "bool",
        "repr",
        "abs",
        "json.dumps",
        "json.loads",
    }
)


def has_effectful_arguments(call: ast.Call) -> bool:
    """引数に、名前・定数・属性の読み出し（path_analyzer と同じく純粋とみなす）以外の式があるか。

    呼出し・await・添字・演算子（`__getitem__` / `__add__` などの上書きを呼び得る）があれば、
    構築を副作用なしとみなさない。
    """
    for a in [*call.args, *(k.value for k in call.keywords)]:
        if isinstance(a, ast.Starred):
            return True
        for n in ast.walk(a):
            if not isinstance(n, ast.Name | ast.Constant | ast.Attribute | ast.expr_context):
                return True
    return False


def _chain(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


__all__ = ["PURE_FUNCTIONS", "StaticTypes", "has_effectful_arguments"]
