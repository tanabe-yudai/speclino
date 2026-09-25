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

from speclino.analysis.symbolic_state import ExprBuilder, Frame, RootAlias, SymState
from speclino.domain.bindings import OperationBinding, OperationContract
from speclino.frontend.python.index import RepoIndex
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
    ) -> None:
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
                if isinstance(sub, ast.Raise):
                    scope.channels.add(OUTCOME_CHANNEL)  # 呼出し先の raise も呼出し元へ伝播する
                elif isinstance(sub, ast.Return):
                    if not nested:
                        scope.channels.add(OUTCOME_CHANNEL)
                elif isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign):
                    self._scan_attribute_writes(sub, frame, state, scope)
                elif isinstance(sub, ast.Call):
                    if not self._scan_call(sub, frame, state, scope, depth):
                        return False
                elif isinstance(sub, ast.Yield | ast.YieldFrom | ast.Await | ast.Global):
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

    def _scan_call(
        self, call: ast.Call, frame: Frame, state: SymState, scope: RegionScope, depth: int
    ) -> bool:
        qual = self.builder.callee_qualname(call.func, frame)
        if qual is None:
            return False
        if qual in self.op_by_symbol:
            op_id, ob = self.op_by_symbol[qual]
            scope.channels.add(f"call:{op_id}")
            contract = self.contracts.get(ob.summary_ref or "")
            if contract is not None:
                # 契約で変更し得ると宣言された変数は、領域が書き得る観測になる
                scope.channels |= {f"write:{var}" for var in contract.may_modify}
            return True
        if qual in self.known_pure:
            return True
        if not self.index.is_function(qual) or depth >= self.depth_limit:
            return False
        callee = self.index.function(qual)
        if function_decorators(callee.node):
            return False
        module = self.index.module(callee.module)
        assert module is not None
        inner = Frame(module)
        # 経路列挙の展開（_inline）と同じく、入口引数の別名だけを呼出し先へ渡す。
        # これで呼出し先での束縛済み属性への書込みが呼出し元の変数に対応づく
        params = [a.arg for a in callee.node.args.args]
        for i, p in enumerate(params):
            node = call.args[i] if i < len(call.args) else _keyword(call, p)
            if isinstance(node, ast.Name) and isinstance(frame.locals.get(node.id), RootAlias):
                inner.locals[p] = frame.locals[node.id]
        return self._scan_stmts(list(callee.node.body), inner, state, scope, depth + 1, nested=True)


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


__all__ = [
    "OPAQUE_STMTS",
    "OUTCOME_CHANNEL",
    "RegionScanner",
    "RegionScope",
    "binding_candidates",
]
