"""構造解析（S29 / ハッカソン計画「構造解析を第二の証拠源に」）。

経路列挙が扱えない複合文（for / while / try / with）を「不透明な領域」として、その AST を
走査し、領域が **触れ得る観測**（チャネル）を求める。

- ``call:<操作 ID>``: 領域内に bindings.operations に束縛された呼出しがある
- ``write:<変数>``: 領域内に bindings.variables に束縛された属性への代入がある
- ``outcome``: 領域内に return / raise があり、経路の終了結果が領域内で決まり得る

領域内に解決できない呼出し（契約の無い外部呼出し・展開深さを超えた自リポジトリ関数）が
あれば、その効果は分からないので None（全てに触れ得る）を返す。

これは支配関係の実用的な形: 領域は経路上で必ず通る 1 点なので、領域が触れないチャネルに
ついては領域の前後で観測した事実（イベントの回数・引数、状態更新、終了結果）がそのまま
成り立つ（健全な不在証明）。条件・値・回数を領域から推定することはしない。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field

from speclino.analysis.static_types import PURE_FUNCTIONS, has_effectful_arguments
from speclino.analysis.symbolic_state import ExprBuilder, Frame, RootAlias, SymState
from speclino.domain.bindings import OperationBinding, OperationContract
from speclino.frontend.python.index import ModuleInfo, RepoIndex
from speclino.frontend.python.subset import function_decorators

OPAQUE_STMTS: tuple[type[ast.stmt], ...] = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
)
OUTCOME_CHANNEL = "outcome"


def binding_candidates(node: ast.AST) -> Iterator[str]:
    """node 自身（子要素は見ない。呼出し側が ast.walk で再帰する）が識別子を束縛し得るなら、
    その文字列を列挙する（S41b / S29）。

    束縛の構文を1つずつ列挙しない: Python の AST では、識別子を束縛し得る文法要素
    （代入先、for/with/match の対象、関数・クラスの名前、import の別名、global/nonlocal
    宣言、except の名前など）は形が様々でも、必ずどこかの文字列フィールド（または文字列の
    リストのフィールド）に識別子文字列を持つ。ast.iter_fields でこれを汎用に拾えば、
    MatchAs のキャプチャのように Name ノードにならない特殊な形も含め、まとめて検出できる。
    Attribute.attr（例 obj.range）のような単なる属性参照も含めて保守的に拾う（安全側）。
    Name(ctx=Load).id（単なる参照）と Constant.value（文字列リテラル。本文中に対象の語が
    出てくるだけの場合）は束縛ではないので除外する。
    """
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            yield node.id
        return
    if isinstance(node, ast.Constant):
        return
    for _, value in ast.iter_fields(node):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (v for v in value if isinstance(v, str))


@dataclass
class RegionScope:
    channels: set[str] = field(default_factory=set)
    assigned_locals: set[str] = field(default_factory=set)  # 領域内で代入されるローカル名

    def touches(self, channel: str) -> bool:
        return channel in self.channels


class RegionScanner:
    """不透明な領域の AST を走査し RegionScope を求める。経路は列挙しない。"""

    def __init__(
        self,
        builder: ExprBuilder,
        index: RepoIndex,
        op_by_symbol: dict[str, tuple[str, OperationBinding]],
        contracts: dict[str, OperationContract],
        known_pure: set[str],
        depth_limit: int,
        expand_operations: frozenset[str] = frozenset(),
    ) -> None:
        self.expand_operations = expand_operations
        self.builder = builder
        self.index = index
        self.op_by_symbol = op_by_symbol
        self.contracts = contracts
        self.known_pure = known_pure
        self.depth_limit = depth_limit

    def scan(self, stmt: ast.stmt, frame: Frame, state: SymState) -> RegionScope | None:
        """None は領域の効果を確定できない（解決不能な呼出しがある）ことを示す。"""
        scope = RegionScope()
        ok = self._scan_stmts([stmt], frame, state, scope, depth=0, nested=False)
        return scope if ok else None

    # ---------------------------------------------------------------- walk
    def _scan_stmts(
        self,
        stmts: list[ast.stmt],
        frame: Frame,
        state: SymState,
        scope: RegionScope,
        depth: int,
        nested: bool,
    ) -> bool:
        for node in stmts:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Raise | ast.Assert):
                    scope.channels.add(OUTCOME_CHANNEL)  # 呼出し先の raise も呼出し元へ伝播する
                elif isinstance(sub, ast.Return):
                    if not nested:
                        scope.channels.add(OUTCOME_CHANNEL)
                elif isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign):
                    self._scan_attribute_writes(sub, frame, state, scope)
                elif isinstance(sub, ast.Call):
                    if not self._scan_call(sub, frame, state, scope, depth):
                        return False
                elif isinstance(sub, ast.Await):
                    # await した呼出しは、その呼出し（ast.walk が続けて訪れる）の効果だけを持つ
                    if not isinstance(sub.value, ast.Call):
                        return False
                elif isinstance(sub, ast.Yield | ast.YieldFrom | ast.Global):
                    return False
                if not nested:
                    # 領域が束縛し得るローカル名を汎用に拾う（for/with/内包表記/match/
                    # NamedExpr 等、構文の形を列挙しない。S41b: 展開を諦めて読み飛ばした
                    # for/while 自身のターゲットもここで拾われる）。読み飛ばし後に
                    # frame.locals から未解決化するための候補集合であり、多少広めに拾っても
                    # （例えば属性名を局所変数と誤認しても）安全側に倒れるだけで健全性は保つ
                    scope.assigned_locals.update(binding_candidates(sub))
        return True

    def _scan_attribute_writes(
        self,
        node: ast.Assign | ast.AnnAssign | ast.AugAssign,
        frame: Frame,
        state: SymState,
        scope: RegionScope,
    ) -> None:
        """代入先が束縛済み属性（例 order.status）なら write:<var> チャネルに加える。

        ローカル名の収集（assigned_locals）は _scan_stmts の汎用パス（binding_candidates）が
        担うので、ここでは属性への書込みだけを見る。
        """
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for leaf in ast.walk(target):
                if isinstance(leaf, ast.Attribute):
                    chain = self.builder.attribute_chain(leaf)
                    if chain is None:
                        continue
                    root = frame.locals.get(chain[0])
                    if isinstance(root, RootAlias):
                        var = state.var_of_impl(".".join([root.root, *chain[1:]]))
                        if var is not None:
                            scope.channels.add(f"write:{var}")

    def _pure_function(self, call: ast.Call, frame: Frame) -> bool:
        return is_pure_function_call(self.builder, call, frame)

    def _scan_call(
        self, call: ast.Call, frame: Frame, state: SymState, scope: RegionScope, depth: int
    ) -> bool:
        cands = self.builder.callee_candidates(call.func, frame)
        if not cands:
            return False
        bound = [self.op_by_symbol[c] for c in cands if c in self.op_by_symbol]
        if bound:
            # 候補が複数の操作に当たるときは、どれにも触れ得るとする（安全側）
            for op_id, ob in bound:
                scope.channels.add(f"call:{op_id}")
                contract = self.contracts.get(ob.summary_ref or "")
                if contract is not None:
                    # 契約で変更し得ると宣言された変数は、領域が書き得る観測になる
                    scope.channels |= {f"write:{var}" for var in contract.may_modify}
            if not any(op_id in self.expand_operations for op_id, _ in bound):
                return True
            # 名前の一致で対応づけた操作は本体の効果も見る（下で走査する）
        elif any(c in self.known_pure for c in cands):
            return True
        elif self._pure_function(call, frame):
            return True  # 引数の中の呼出しは ast.walk が続けて訪れる
        else:
            chain = self.builder.attribute_chain(call.func)
            if chain is not None and chain[0] not in frame.locals:
                ctor = self.builder.types.class_of(call.func, frame.module)
                if ctor is not None and self.builder.types.trivial_class(ctor):
                    return True  # 引数の中の呼出しは ast.walk が続けて訪れる
        # 本体に入るのは名前で解決した関数か、展開する操作の宣言型のメソッドだけ（宣言型の
        # メソッドを self なしで走査すると引数がずれる）
        target = self.builder.body_target(call, frame, cands, typed_ok=bool(bound))
        if target is None or depth >= self.depth_limit or _has_unpacking(call):
            return False
        if target[1] is not None and not isinstance(target[1], RootAlias):
            return False  # 受け手が入口の引数を根に持たない: self の効果を走査で追えない
        callee = self.index.function(target[0])
        if function_decorators(callee.node):
            return False
        module = self.index.module(callee.module)
        assert module is not None
        inner = Frame(module)
        # 経路列挙の展開（_inline）と同じく、入口引数の別名だけを呼出し先へ渡す。
        # これで呼出し先での束縛済み属性への書込みが呼出し元の変数に対応づく
        args = callee.node.args
        positional = [a.arg for a in [*args.posonlyargs, *args.args]]
        if target[1] is not None:
            if not positional:
                return False
            inner.bind(positional[0], target[1])
            positional = positional[1:]
        for i, p in enumerate([*positional, *(a.arg for a in args.kwonlyargs)]):
            node = call.args[i] if i < len(positional) and i < len(call.args) else _keyword(call, p)
            if isinstance(node, ast.Name) and isinstance(frame.locals.get(node.id), RootAlias):
                inner.bind(p, frame.locals[node.id])
        return self._scan_stmts(list(callee.node.body), inner, state, scope, depth + 1, nested=True)


_BUILTINS_MODULE_NAMES = ("builtins", "__builtins__")


def module_may_rebind(mod: ModuleInfo, name: str, *, allow_import: str | None = None) -> bool:
    """モジュールのどこか（最上位に限らず、if/try/with/for/関数・クラス・match 本体の中も
    含む）で name が再束縛され得るか。分からなければ True（安全側）。

    束縛の構文を1つずつ列挙しない（MatchAs のキャプチャのような Name ノードにならない特殊な
    形も見落とさないため）。``binding_candidates``（structural.py、S29 の RegionScanner と
    共有）が ast.iter_fields で全フィールドを汎用に見て、識別子を束縛し得る文字列を返す。
    ``from x import *`` はどの名前を束縛するか静的に分からないので無条件に対象とし、
    ``builtins``/``__builtins__`` への参照は setattr 等で外から書き換えられ得るため
    1 つでもあれば無条件に対象とする（この 2 つは「name と一致するか」では判定できない）。
    allow_import を渡すと、モジュールの最上位の `import <allow_import>`（別名なし）だけは数えない。
    """
    allowed = {
        id(a)
        for stmt in mod.tree.body
        if allow_import is not None and isinstance(stmt, ast.Import)
        for a in stmt.names
        if a.name == allow_import and a.asname is None
    }
    for node in ast.walk(mod.tree):
        if id(node) in allowed:
            continue
        if isinstance(node, ast.alias) and node.name == "*":
            return True
        if isinstance(node, ast.Name) and node.id in _BUILTINS_MODULE_NAMES:
            return True
        if name in binding_candidates(node):
            return True
    return False


# json の関数で許すキーワード引数（default・cls・object_hook などは任意の関数を呼び得る）
_JSON_KEYWORDS = frozenset(
    {"indent", "sort_keys", "ensure_ascii", "separators", "allow_nan", "skipkeys"}
)


def is_pure_function_call(builder: ExprBuilder, call: ast.Call, frame: Frame) -> bool:
    """`PURE_FUNCTIONS` の呼出しで、引数も純粋なもの（経路の解析と領域の走査で同じ判定）。

    組込みの名前はモジュールで再束縛され得ないこと。`json.dumps` のようなドットを含む名前は、
    根が `import json` で束縛され再束縛され得ないこと。
    json の関数のキーワード引数は許可した名前だけ。
    """
    chain = builder.attribute_chain(call.func)
    if chain is None or chain[0] in frame.locals or has_effectful_arguments(call):
        return False
    if builder.callee_qualname(call.func, frame) not in PURE_FUNCTIONS:
        return False
    if len(chain) == 1:
        return not module_may_rebind(frame.module, chain[0])
    if len(chain) != 2 or frame.module.imports.get(chain[0]) != "json":
        return False
    if module_may_rebind(frame.module, chain[0], allow_import="json"):
        return False
    return all(k.arg in _JSON_KEYWORDS for k in call.keywords)


def _has_unpacking(call: ast.Call) -> bool:
    """`f(*xs)` / `f(**kw)`（実引数と引数の対応が静的に決まらない）。"""
    return any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    )


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


__all__ = [
    "OPAQUE_STMTS",
    "is_pure_function_call",
    "module_may_rebind",
    "OUTCOME_CHANNEL",
    "RegionScanner",
    "RegionScope",
    "binding_candidates",
]
