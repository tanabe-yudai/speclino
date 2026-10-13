"""path_analyzer。対応構文だけで書かれた入口関数から Code IR を作る。

経路を分岐させながら記号的状態を更新し、guard / state_updates / events / outcome を持つ
Path を列挙する。早期 return より後を実行済みにしない。呼出しは bindings.operations の
契約、リポジトリ内関数のローカル要約（深さ上限）、それ以外は未解決として記録する。
対象リポジトリのモジュールを import しない。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from pathlib import Path as FsPath
from typing import cast, get_args

from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.analysis.static_types import has_effectful_arguments
from speclino.analysis.structural import (
    OPAQUE_STMTS,
    OUTCOME_CHANNEL,
    RegionScanner,
    is_pure_function_call,
    module_may_rebind,
)
from speclino.analysis.symbolic_state import (
    ExprBuilder,
    Frame,
    LocalValue,
    RootAlias,
    SymState,
    Unresolved,
    guard_expr,
)
from speclino.domain.bindings import Bindings, OperationBinding, OperationContract
from speclino.domain.code_ir import (
    AnalysisLimits,
    CodeIR,
    Event,
    Outcome,
    Path,
    StateUpdate,
    UnknownCause,
    UnknownCode,
)
from speclino.domain.common import SnapshotRef, SourceRef
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Expr
from speclino.frontend.python.adapters.fastapi_outcome import (
    outcome_of_raise,
    outcome_of_return_call,
)
from speclino.frontend.python.index import FunctionInfo, RepoIndex, SymbolNotFound
from speclino.frontend.python.subset import Unsupported, function_decorators, unsupported_stmt

ANALYZER_VERSION = "0.1.0"

# 定数回反復として展開できる要素数の上限（S41b）。ネストした展開も含め、1 つの経路（Draft）で
# ここまでに消費した展開回数の累積で数える（Draft.unroll_used。if で分岐した枝ごとに独立に
# 数えるので、無関係な枝の展開が互いの予算を奪わない）。経路数（64/256）や
# 呼出し深さ（2/4）の初期上限と同じく保守的な初期値とし、監査イベントの列挙や
# 固定件数のアイテム処理など数件程度の反復は展開できる一方、上限を超えたら展開せず
# 既存の安全網（unsupported_syntax による UNKNOWN、展開後に経路数が増え過ぎた場合は
# limit_paths による UNKNOWN）に委ねる。
UNROLL_MAX_ITERATIONS = 8

# 展開時に要素式を退避する一時変数の名前の型。``$`` と ``.`` は Python の識別子に使えない
# 文字なので、対象コード（ast.parse を通った時点で識別子として妥当）のどんな変数名とも
# 原理的に衝突しない。{seq} は展開（for 文）ごとの通し番号、{i} は要素の位置。
_UNROLL_TEMP_NAME = "$unroll{seq}.{i}"

_VALID_UNKNOWN_CODES = frozenset(get_args(UnknownCode))


def _safe_unknown_code(code: str) -> UnknownCode:
    """UnknownCause.code に渡す前に、正当な値に丸める。

    Unresolved.code（symbolic_state.py）は自由な str で、UnknownCause.code（pydantic の
    Literal）の値集合と一致する保証が無い（例: _assign が呼出し結果のローカルに付ける
    "unresolved_call" 以外の内部専用の印）。丸めずに UnknownCause(code=...) へ渡すと
    pydantic の検証で例外になり、解析全体が失敗する。値が不明なら安全側の
    unsupported_syntax に倒し、「解析できないものを違反と表示しない」を守る。
    """
    if code in _VALID_UNKNOWN_CODES:
        return cast(UnknownCode, code)
    return "unsupported_syntax"


class PathLimitExceeded(Exception):
    pass


@dataclass
class Draft:
    """探索中の経路。ended は raise / return / fallthrough / unresolved。"""

    conds: list[Expr]
    state: SymState
    updates: list[StateUpdate] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    unknowns: list[UnknownCause] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    unroll_used: int = 0  # S41b: この経路でここまでに消費した定数回ループの展開回数
    ended: str = "fallthrough"
    outcome: Outcome | None = None
    return_value: Expr | None = None

    def fork(self, cond: Expr) -> Draft:
        return Draft(
            [*self.conds, cond],
            self.state.copy(),
            list(self.updates),
            list(self.events),
            list(self.unknowns),
            list(self.lines),
            self.unroll_used,
        )


@dataclass(frozen=True)
class _UnrollOutcome:
    """for の展開結果。``stmts`` が None なら展開できない（``reason`` に理由）。"""

    stmts: list[ast.stmt] | None
    reason: str = ""


def _has_break_or_continue(body: list[ast.stmt]) -> bool:
    """body（ネストした for/while/AsyncFor 自身のスコープは除く）に break/continue があるか。

    break/continue は最も内側の for/while に属するので、ネストしたループの本体には再帰しない。
    if / try / with の中は同じループのスコープなので再帰する。それ以外の対応外構文（match 等）の
    中は再帰しないが、展開後にその構文自体が unsupported_stmt で打ち切られるので健全性には
    影響しない。
    """
    for stmt in body:
        if isinstance(stmt, ast.Break | ast.Continue):
            return True
        if isinstance(stmt, ast.If):
            if _has_break_or_continue(stmt.body) or _has_break_or_continue(stmt.orelse):
                return True
        elif isinstance(stmt, ast.Try):
            if (
                _has_break_or_continue(stmt.body)
                or _has_break_or_continue(stmt.orelse)
                or _has_break_or_continue(stmt.finalbody)
                or any(_has_break_or_continue(h.body) for h in stmt.handlers)
            ):
                return True
        elif isinstance(stmt, ast.With | ast.AsyncWith) and _has_break_or_continue(stmt.body):
            return True
        # For / AsyncFor / While はそれ自身のスコープなので再帰しない
    return False


def _for_elements(it: ast.expr, frame: Frame) -> list[ast.expr] | str:
    """反復要素を式の列として返す（要素は評価しない）。展開できなければ理由を返す。

    range(定数) は要素（ast.Constant）を作る前に長さを展開上限と比べる。解析対象コードに
    range(10**9) のような巨大な引数があっても、展開を諦める判定の前に巨大な list を作らない
    ようにするため。range が組込みだと確定できない（対象モジュールのどこかで再定義され得る）
    ときは展開しない。
    """
    if isinstance(it, ast.Tuple | ast.List):
        if any(isinstance(e, ast.Starred) for e in it.elts):
            return "要素に展開（*）がある"
        return list(it.elts)
    if (
        isinstance(it, ast.Call)
        and isinstance(it.func, ast.Name)
        and it.func.id == "range"
        and "range" not in frame.locals
    ):
        if module_may_rebind(frame.module, "range"):
            return "range が組込みと確定できない（モジュールのどこかで再定義され得る）"
        if it.keywords or any(isinstance(a, ast.Starred) for a in it.args):
            return "range の引数の形が対応外（キーワード引数・展開）"
        if not (1 <= len(it.args) <= 2):
            return "range の引数の形が対応外（step 指定など）"
        values: list[int] = []
        for a in it.args:
            if not (
                isinstance(a, ast.Constant)
                and isinstance(a.value, int)
                and not isinstance(a.value, bool)
            ):
                return "range の引数が定数 int でない"
            values.append(a.value)
        start, stop = (0, values[0]) if len(values) == 1 else (values[0], values[1])
        if start < 0 or stop < 0:
            return "range の引数に負の値がある"
        if stop - start > UNROLL_MAX_ITERATIONS:
            return f"range の要素数が展開上限（{UNROLL_MAX_ITERATIONS}）を超える"
        return [ast.Constant(value=n) for n in range(start, stop)]
    return "要素数が静的に分からない反復（タプル/リストのリテラルでも range(定数) でもない）"


def _make_assign(stmt: ast.For, name: str, value: ast.expr) -> ast.Assign:
    """``name = value`` を合成する。位置情報は元の for からコピーする。"""
    assign = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)
    ast.copy_location(assign, stmt)
    ast.fix_missing_locations(assign)
    return assign


class PathAnalyzer:
    def __init__(
        self,
        index: RepoIndex,
        domain: DomainSpec,
        bindings: Bindings,
        contracts: dict[str, OperationContract] | None = None,
        limits: AnalysisLimits = DEFAULT_LIMITS,
        snapshot: SnapshotRef | None = None,
        expand_operations: frozenset[str] = frozenset(),
    ) -> None:
        self.index = index
        # 呼出しイベントを出したうえで本体も展開する操作（名前の一致で対応づけた操作。
        # bindings で宣言した操作と違い、本体の効果を「無い」と仮定しない）
        self.expand_operations = expand_operations
        self.domain = domain
        self.bindings = bindings
        self.contracts = contracts or {}
        self.limits = limits
        self.snapshot = snapshot or SnapshotRef(snapshot_id="local")
        self.builder = ExprBuilder(index, domain)
        self._op_by_symbol = {ob.symbol: (op_id, ob) for op_id, ob in bindings.operations.items()}
        self._known_pure = set(bindings.known_pure)
        self._path_count = 0
        self._unroll_seq = 0  # S41b: 展開ごとに振る通し番号（退避用の一時変数名の衝突回避）
        self.scanner = RegionScanner(
            self.builder,
            index,
            self._op_by_symbol,
            self.contracts,
            self._known_pure,
            limits.depth,
            expand_operations,
        )

    # ------------------------------------------------------------------ public
    def analyze(self, behavior_id: str) -> CodeIR:
        binding = self.bindings.behaviors[behavior_id]
        top_unknowns: list[UnknownCause] = []
        try:
            fn = self.index.function(binding.entrypoint)
        except SymbolNotFound as e:
            return self._empty(behavior_id, binding.entrypoint, "unresolved_call", str(e))
        if function_decorators(fn.node):
            return self._empty(
                behavior_id,
                binding.entrypoint,
                "unsupported_syntax",
                f"デコレーター {function_decorators(fn.node)} は対応外",
                fn,
            )
        module = self.index.module(fn.module)
        assert module is not None
        frame = Frame(module)
        types = self.builder.types.param_types(fn)
        for arg in _params(fn.node):
            frame.bind(arg, RootAlias(arg), types.get(arg))
        self._path_count = 0
        self._unroll_seq = 0
        start = Draft([], SymState(self.domain, binding))
        try:
            drafts = self._explore(list(fn.node.body), fn, frame, start, depth=0)
        except PathLimitExceeded:
            drafts = []
            top_unknowns.append(
                UnknownCause(
                    code="limit_paths",
                    detail=f"経路数が上限 {self.limits.max_paths} を超えた",
                    source_ref=self._src(fn, fn.line_start),
                )
            )
        paths = [self._to_path(i + 1, d, fn) for i, d in enumerate(drafts)]
        return CodeIR(
            behavior_id=behavior_id,
            entrypoint=binding.entrypoint,
            snapshot=self.snapshot,
            analyzer_version=ANALYZER_VERSION,
            limits=self.limits,
            paths=paths,
            unknown_causes=top_unknowns,
        )

    # ---------------------------------------------------------------- explore
    def _explore(
        self, stmts: list[ast.stmt], fn: FunctionInfo, frame: Frame, d: Draft, depth: int
    ) -> list[Draft]:
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            rest = stmts[i + 1 :]
            unroll_reason: str | None = None
            if isinstance(stmt, ast.For):
                outcome = self._try_unroll_for(stmt, frame, d)
                if outcome.stmts is not None:
                    # 純粋な AST 書き換え: x = e1; *body; x = e2; *body; ...; *orelse に差し替えて
                    # 同じ i から続ける（解析器に新しい意味論は足さない）
                    stmts = stmts[:i] + outcome.stmts + rest
                    continue
                unroll_reason = outcome.reason
            elif isinstance(stmt, ast.While):
                unroll_reason = "while は定数回の反復として展開できない"
            if isinstance(stmt, ast.Assert):
                # `assert t, m` は「t が偽なら AssertionError で抜ける」分岐と同じ（純粋な AST の
                # 書き換え。python -O で assert を外す実行はモデルの外）
                fail = ast.Raise(
                    exc=ast.Call(
                        func=ast.Name(id="AssertionError", ctx=ast.Load()), args=[], keywords=[]
                    ),
                    cause=None,
                )
                branch = ast.If(test=stmt.test, body=[ast.Pass()], orelse=[fail])
                ast.copy_location(branch, stmt)
                ast.fix_missing_locations(branch)
                stmts = stmts[:i] + [branch] + rest
                continue
            d.lines.append(stmt.lineno)
            if (u := unsupported_stmt(stmt)) is not None:
                if unroll_reason:
                    u = replace(u, detail=f"{u.detail}（展開できない: {unroll_reason}）")
                if self._skip_opaque_region(stmt, u, fn, frame, d):
                    i += 1
                    continue
                self._unknown(d, "unsupported_syntax", u.detail, fn, u.lineno)
                d.ended, d.outcome = "unresolved", Outcome(kind="unresolved")
                return [self._finish(d)]
            if self._returns_expanded_operation(stmt, frame):
                # `return <展開する操作の呼出し>` は `$ret = <呼出し>; return $ret` と同じ（本体を
                # 展開した後で呼出し元が戻る）。純粋な AST の書き換えで、意味論は足さない
                assert isinstance(stmt, ast.Return) and stmt.value is not None
                tmp = f"$ret{self._unroll_seq}"
                self._unroll_seq += 1
                assign = ast.copy_location(
                    ast.Assign(targets=[ast.Name(id=tmp, ctx=ast.Store())], value=stmt.value), stmt
                )
                ret = ast.copy_location(ast.Return(value=ast.Name(id=tmp, ctx=ast.Load())), stmt)
                stmts = stmts[:i] + [assign, ret] + rest
                d.lines.pop()
                continue
            match stmt:
                case ast.If():
                    cond = self.builder.to_expr(stmt.test, frame, d.state, "boolean")
                    if isinstance(cond, Unresolved):
                        self._unknown(d, cond.code, cond.detail, fn, cond.lineno)
                        d.ended, d.outcome = "unresolved", Outcome(kind="unresolved")
                        return [self._finish(d)]
                    then_d = d.fork(cond)
                    else_d = d.fork(_negate(cond))
                    out = self._explore([*stmt.body, *rest], fn, frame.copy(), then_d, depth)
                    out += self._explore([*stmt.orelse, *rest], fn, frame.copy(), else_d, depth)
                    return out
                case ast.Assign() | ast.AnnAssign():
                    ended = self._assign(stmt, fn, frame, d, depth, rest)
                    if ended is not None:
                        return ended
                case ast.Expr(value=ast.Call() as call):
                    ended = self._call(call, fn, frame, d, depth, rest)
                    if ended is not None:
                        return ended
                case ast.Expr(value=ast.Await(value=ast.Call() as call)):
                    ended = self._call(call, fn, frame, d, depth, rest, awaited=True)
                    if ended is not None:
                        return ended
                case ast.Expr(value=ast.Await()):
                    self._unknown(
                        d,
                        "unresolved_call",
                        f"呼出し以外の await は対応外: {ast.unparse(stmt.value)}",
                        fn,
                        stmt.lineno,
                    )
                case ast.Expr():
                    # `cond and op()` のような式文の中の呼出しは順序を追えない
                    self._record_expression_calls(stmt.value, fn, frame, d)
                case ast.Raise():
                    self._raise(stmt, fn, frame, d)
                    return [self._finish(d)]
                case ast.Return():
                    self._return(stmt, fn, frame, d)
                    return [self._finish(d)]
                case ast.Pass():
                    pass
            i += 1
        d.ended = "fallthrough"
        d.outcome = Outcome(kind="return")
        return [self._finish(d)]

    def _skip_opaque_region(
        self, stmt: ast.stmt, u: Unsupported, fn: FunctionInfo, frame: Frame, d: Draft
    ) -> bool:
        """対応外の複合文を、触れ得る観測を記録した上で読み飛ばせるか（S29 構造解析）。

        終了結果（return / raise）や束縛変数の書込みに触れ得る領域、効果を確定できない領域は
        読み飛ばさず、従来どおり経路を打ち切る。読み飛ばすときは領域内で代入されたローカルを
        未解決にし、以後の参照で経路が打ち切られるようにする。
        """
        if not isinstance(stmt, OPAQUE_STMTS):
            return False
        scope = self.scanner.scan(stmt, frame, d.state)
        if scope is None or OUTCOME_CHANNEL in scope.channels:
            return False
        if any(c.startswith("write:") for c in scope.channels):
            return False
        touched = ", ".join(sorted(scope.channels)) or "なし"
        self._unknown(
            d,
            "unsupported_syntax",
            f"{u.detail}（構造解析: 領域が触れ得る観測は {touched}）",
            fn,
            u.lineno,
            may_affect=sorted(scope.channels),
        )
        for name in scope.assigned_locals:
            frame.bind(
                name,
                Unresolved(
                    "unsupported_syntax",
                    f"{name} は対応外の領域（行 {u.lineno}）で代入される",
                    u.lineno,
                ),
            )
        return True

    # ------------------------------------------------------------ loop unroll
    def _try_unroll_for(self, stmt: ast.For, frame: Frame, d: Draft) -> _UnrollOutcome:
        """定数回の for を展開できるか判定し、できれば置き換え後の文の列を返す（S41b）。

        Python は反復の全要素をループ開始前に評価する。これを保つため、要素式はまず一時変数へ
        退避してから（``_t0 = e1; _t1 = e2; ...``）ループ変数に束縛する
        （``x = _t0; *body; x = _t1; *body; ...; *orelse``）。body の実行前に退避を終えるので、
        body が要素式の参照先を書き換えても後続要素は元の値のまま、要素式が副作用を持つ呼出しでも
        1 回だけ・ループより前に実行される（body が途中で return / raise しても後続要素の
        呼出しが失われない）。一時変数名（_UNROLL_TEMP_NAME）は Python の識別子として書けない
        文字（``$`` ``.``）を含むため、対象コードのどんな変数名とも原理的に衝突しない
        （対象コードの識別子は ast.parse を通った時点で妥当な Python 識別子であることが
        保証されている）。要素の式は評価しない（解釈は _assign → ExprBuilder.to_expr に
        委ねる）。展開できないときは理由付きで None を返し、呼出し元は従来どおり
        unsupported_stmt / 構造解析に任せる。展開回数は d.unroll_used（この経路でここまでに
        消費した分）に対して数える。
        """
        if not isinstance(stmt.target, ast.Name):
            return _UnrollOutcome(None, "target がタプル分解など単純な変数名でない")
        target = stmt.target
        if _has_break_or_continue(stmt.body):
            return _UnrollOutcome(None, "本体に break/continue がある")
        elements = _for_elements(stmt.iter, frame)
        if isinstance(elements, str):
            return _UnrollOutcome(None, elements)
        if d.unroll_used + len(elements) > UNROLL_MAX_ITERATIONS:
            return _UnrollOutcome(
                None, f"展開回数の上限（{UNROLL_MAX_ITERATIONS}）をネスト分含めて超える"
            )
        d.unroll_used += len(elements)
        seq = self._unroll_seq
        self._unroll_seq += 1
        temp_names = [_UNROLL_TEMP_NAME.format(seq=seq, i=i) for i in range(len(elements))]
        built: list[ast.stmt] = [
            _make_assign(stmt, tmp, elt) for tmp, elt in zip(temp_names, elements, strict=True)
        ]
        for tmp in temp_names:
            built.append(_make_assign(stmt, target.id, ast.Name(id=tmp, ctx=ast.Load())))
            built.extend(stmt.body)
        built.extend(stmt.orelse)
        return _UnrollOutcome(built)

    def _finish(self, d: Draft) -> Draft:
        self._path_count += 1
        if self._path_count > self.limits.max_paths:
            raise PathLimitExceeded
        return d

    # ------------------------------------------------------------- statements
    def _assign(
        self,
        stmt: ast.Assign | ast.AnnAssign,
        fn: FunctionInfo,
        frame: Frame,
        d: Draft,
        depth: int,
        rest: list[ast.stmt],
    ) -> list[Draft] | None:
        """代入文。右辺が呼出しなら副作用（イベント／展開）を取りこぼさない。"""
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                self._unknown(d, "unsupported_syntax", "多重代入は対応外", fn, stmt.lineno)
                return None
            target, value_node = stmt.targets[0], stmt.value
        else:
            if stmt.value is None:
                return None
            target, value_node = stmt.target, stmt.value
        if isinstance(target, ast.Tuple | ast.List):
            return self._unpack(target, value_node, fn, frame, d, depth, rest)
        if isinstance(target, ast.Name):
            awaited = isinstance(value_node, ast.Await) and isinstance(value_node.value, ast.Call)
            if isinstance(value_node, ast.Await) and isinstance(value_node.value, ast.Call):
                value_node = value_node.value
            if isinstance(value_node, ast.Call):
                cands = self.builder.callee_candidates(value_node.func, frame)
                pure_vars = {
                    d.state.pure_call_vars[c] for c in cands if c in d.state.pure_call_vars
                }
                if len(pure_vars) == 1 and not awaited:
                    # 純粋な呼出しの結果は名前付き変数（副作用なし、イベントなし）
                    frame.bind(target.id, d.state.value(next(iter(pure_vars))))
                    return None
                # code は UnknownCode の正当な値にする（_unknown 経由で UnknownCause.code に
                # そのまま渡り得るため。例えばこの変数を後で if の条件に使うと、その時点の
                # cond.code がそのまま _unknown へ渡る）。analyzed_call=True は「_call を通して
                # 副作用（イベント・展開）を記録し終えた」印（_return がこれを見て「値は追えない
                # がサービス層の戻り値として経路は完全」と判定する。下記参照）。式の中の未解析の
                # 呼出し（ExprBuilder.to_expr の ast.Call() 分岐）は同じ code を使うが
                # analyzed_call は False のまま、というのが両者を区別する唯一の手段
                result: list[_Binding] = [
                    (
                        target.id,
                        Unresolved(
                            "unresolved_call",
                            f"呼出し結果: {ast.unparse(value_node)}",
                            value_node.lineno,
                            analyzed_call=True,
                        ),
                        self._result_type(cands),
                    )
                ]
                # 展開（_inline）したときは残りの文を呼出し先の中で続けるので、結果の束縛も渡す
                ended = self._call(
                    value_node, fn, frame, d, depth, rest, awaited=awaited, result=result
                )
                for b in result:
                    frame.bind(*b)
                return ended
            self._record_expression_calls(value_node, fn, frame, d)
            value = self.builder.to_expr(value_node, frame, d.state)
            frame.bind(target.id, value)
            return None
        chain = self.builder.attribute_chain(target)
        if chain is None or chain[0] not in frame.locals:
            self._unknown(
                d,
                "unresolved_attribute",
                f"代入先 {ast.unparse(target)} を解決できない",
                fn,
                stmt.lineno,
            )
            return None
        root = frame.locals[chain[0]]
        if not isinstance(root, RootAlias):
            self._unknown(
                d,
                "unresolved_attribute",
                f"代入先 {ast.unparse(target)} の根が別名でない",
                fn,
                stmt.lineno,
            )
            return None
        var = d.state.var_of_impl(".".join([root.root, *chain[1:]]))
        if var is None:
            self._unknown(
                d,
                "unresolved_attribute",
                f"代入先 {ast.unparse(target)} は bindings に無い",
                fn,
                stmt.lineno,
            )
            return None
        value = self.builder.to_expr(value_node, frame, d.state, self.domain.type_of(var))
        if isinstance(value, Unresolved):
            self._unknown(d, value.code, value.detail, fn, value.lineno)
            return None
        src = self._src(fn, stmt.lineno)
        d.state.assign(var, value)
        d.updates.append(StateUpdate(target=var, value=value, source_ref=src))
        d.events.append(
            Event(
                sequence=len(d.events) + 1,
                kind="write",
                phase="applied",
                target=var,
                value=value,
                source_ref=src,
            )
        )
        return None

    def _call(
        self,
        call: ast.Call,
        fn: FunctionInfo,
        frame: Frame,
        d: Draft,
        depth: int,
        rest: list[ast.stmt],
        *,
        awaited: bool = False,
        result: list[_Binding] | None = None,
    ) -> list[Draft] | None:
        """呼出し文。経路が終了（呼出し先で raise）したときは終了済み Draft 群を返す。

        `await f(...)` は f の呼出しとして扱う（呼出し元の処理の順序は変わらない。await の間に
        他のタスクが入口の引数の属性を書き換えることはモデルの外）。async def を await せずに
        呼ぶ（coroutine を作るだけで本体を実行しない）と、効果を確定できないので未解決にする。
        """
        cands = self.builder.callee_candidates(call.func, frame)
        if not cands:
            self._unknown(
                d,
                "unresolved_call",
                f"呼出し先を解決できない: {ast.unparse(call.func)}",
                fn,
                call.lineno,
            )
            return None
        qual = cands[0]
        matched = self._match_operation(cands)
        if len(matched) > 1:
            ops = sorted(matched)
            self._unknown(
                d,
                "unresolved_call",
                f"呼出し {ast.unparse(call.func)} が複数の操作 {', '.join(ops)} に対応する"
                "（bindings の対応が重なっている）",
                fn,
                call.lineno,
                may_affect=[f"call:{op}" for op in ops],
            )
            return None
        if matched:
            op_id, ob = next(iter(matched.values()))
            if not awaited and self._is_async(cands):
                self._unknown(
                    d,
                    "unresolved_call",
                    f"async の操作 {op_id} を await せずに呼んでいる（実行の有無を確定できない）",
                    fn,
                    call.lineno,
                    may_affect=[f"call:{op_id}"],
                )
                return None
            self._emit_operation(op_id, ob, call, fn, frame, d)
            if op_id not in self.expand_operations:
                return None
            target = self.builder.body_target(call, frame, cands, typed_ok=True)
            if target is None or self.index.function(target[0]).is_async != awaited:
                self._unknown(
                    d,
                    "unresolved_call",
                    f"名前の一致で対応づけた操作 {op_id} の本体を展開できない"
                    "（効果を確定できない）",
                    fn,
                    call.lineno,
                )
                return None
            return self._inline(target[0], call, fn, frame, d, depth, rest, result, target[1])
        if any(c in self._known_pure for c in cands):
            return None
        if not awaited and self._pure_function(call, frame):
            return None
        if not awaited and self._trivial_construction(call, frame):
            return None  # 副作用の無い構築（static_types の「自明な構築」）
        # 展開するのは名前で解決した関数だけ（宣言型で解決したメソッドは self の束縛を持たない
        # ので展開しない。操作の照合にだけ使う）
        if qual == self.builder.callee_qualname(call.func, frame) and self.index.is_function(qual):
            callee_async = self.index.function(qual).is_async
            if callee_async != awaited:
                self._unknown(
                    d,
                    "unresolved_call",
                    f"{qual} の呼出しと await が対応しない（async def を await しない、"
                    "または同期関数の結果を await する）",
                    fn,
                    call.lineno,
                )
                return None
            return self._inline(qual, call, fn, frame, d, depth, rest, result)
        self._unknown(d, "unresolved_call", f"契約の無い外部呼出し: {qual}", fn, call.lineno)
        return None

    def _unpack(
        self,
        target: ast.Tuple | ast.List,
        value_node: ast.expr,
        fn: FunctionInfo,
        frame: Frame,
        d: Draft,
        depth: int,
        rest: list[ast.stmt],
    ) -> list[Draft] | None:
        """分解代入 `a, b = ...`。対象は名前だけ（`*` なし）。

        右辺が同じ長さのタプル・リストなら要素ごとの代入（右辺を先に全部評価するので、値はどれも
        代入前の状態で作る）。右辺が呼出しなら呼出しを解析し、各名前は値の分からない結果にする。
        それ以外は対応外。
        """
        names = [e.id for e in target.elts if isinstance(e, ast.Name)]
        if len(names) != len(target.elts) or len(set(names)) != len(names):
            self._unknown(
                d,
                "unsupported_syntax",
                f"分解代入の形が対応外: {ast.unparse(target)}",
                fn,
                target.lineno,
            )
            return None
        if isinstance(value_node, ast.Tuple | ast.List) and len(value_node.elts) == len(names):
            if any(isinstance(e, ast.Starred) for e in value_node.elts):
                self._unknown(d, "unsupported_syntax", "右辺の展開（*）は対応外", fn, target.lineno)
                return None
            for e in value_node.elts:
                self._record_expression_calls(e, fn, frame, d)
            values = [self.builder.to_expr(e, frame, d.state) for e in value_node.elts]
            for name, value in zip(names, values, strict=True):
                frame.bind(name, value)
            return None
        awaited = isinstance(value_node, ast.Await) and isinstance(value_node.value, ast.Call)
        call = value_node.value if isinstance(value_node, ast.Await) else value_node
        if not isinstance(call, ast.Call):
            self._unknown(
                d,
                "unsupported_syntax",
                f"分解代入の右辺が対応外: {ast.unparse(value_node)}",
                fn,
                target.lineno,
            )
            return None
        result: list[_Binding] = [
            (
                name,
                Unresolved(
                    "unresolved_call",
                    f"呼出し結果の要素: {ast.unparse(call)}",
                    call.lineno,
                    analyzed_call=True,
                ),
                None,
            )
            for name in names
        ]
        ended = self._call(call, fn, frame, d, depth, rest, awaited=awaited, result=result)
        for b in result:
            frame.bind(*b)
        return ended

    def _returns_expanded_operation(self, stmt: ast.stmt, frame: Frame) -> bool:
        if not self.expand_operations or not isinstance(stmt, ast.Return):
            return False
        value = stmt.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            return False
        cands = self.builder.callee_candidates(value.func, frame)
        return any(op in self.expand_operations for op in self._match_operation(cands))

    def _record_expression_calls(
        self, node: ast.expr, fn: FunctionInfo, frame: Frame, d: Draft
    ) -> None:
        """式の中の呼出し（文として解析しないもの）を未解決として記録する。

        操作に当たる呼出しは、呼ばれるか・何回かを経路で追えないので、その操作に触れる観測を
        妨げる。解決できない呼出し・呼出し以外の await は効果が分からないので全てを妨げる。
        純粋な標準の関数・自明な構築・既知の純粋な呼出し・解決できて操作でもない関数は従来どおり
        （式の値が未解決になり、使えば経路が打ち切られる）。
        """
        for sub in ast.walk(node):
            if isinstance(sub, ast.Await) and not isinstance(sub.value, ast.Call):
                self._unknown(
                    d, "unresolved_call", f"式の中の await: {ast.unparse(sub)}", fn, sub.lineno
                )
                continue
            if not isinstance(sub, ast.Call):
                continue
            cands = self.builder.callee_candidates(sub.func, frame)
            ops = sorted(self._match_operation(cands))
            if ops:
                self._unknown(
                    d,
                    "unresolved_call",
                    f"式の中の操作の呼出し {ast.unparse(sub.func)}（順序と回数を追えない）",
                    fn,
                    sub.lineno,
                    may_affect=[f"call:{op}" for op in ops],
                )
                continue
            if (
                any(c in self._known_pure for c in cands)
                or self._pure_function(sub, frame)
                or self._trivial_construction(sub, frame)
                or (cands and self.index.is_function(cands[0]))
            ):
                continue
            self._unknown(
                d,
                "unresolved_call",
                f"式の中の解決できない呼出し: {ast.unparse(sub.func)}",
                fn,
                sub.lineno,
            )

    def _pure_function(self, call: ast.Call, frame: Frame) -> bool:
        return is_pure_function_call(self.builder, call, frame)

    def _trivial_construction(self, call: ast.Call, frame: Frame) -> bool:
        chain = self.builder.attribute_chain(call.func)
        if chain is None or chain[0] in frame.locals:
            return False
        ctor = self.builder.types.class_of(call.func, frame.module)
        return (
            ctor is not None
            and self.builder.types.trivial_class(ctor)
            and not has_effectful_arguments(call)
        )

    def _match_operation(self, cands: list[str]) -> dict[str, tuple[str, OperationBinding]]:
        """候補のどれかに束縛された操作（操作 ID → (ID, 束縛)）。2 つ以上なら対応が重なっている。"""
        return {
            self._op_by_symbol[c][0]: self._op_by_symbol[c]
            for c in cands
            if c in self._op_by_symbol
        }

    def _is_async(self, cands: list[str]) -> bool:
        return any(self.index.is_function(c) and self.index.function(c).is_async for c in cands)

    def _result_type(self, cands: list[str]) -> str | None:
        """呼出し結果の宣言型: 構築ならそのクラス、関数ならの戻り値の注釈。"""
        for c in cands:
            if self.index.class_def(c) is not None:
                return c
            ty = self.builder.types.return_type(c)
            if ty is not None:
                return ty
        return None

    def _emit_operation(
        self,
        op_id: str,
        ob: OperationBinding,
        call: ast.Call,
        fn: FunctionInfo,
        frame: Frame,
        d: Draft,
    ) -> None:
        """契約済み操作の呼出しイベント。引数は argument_bindings に従って解決する。"""
        args: dict[str, Expr] = {}
        for name, spec in ob.argument_bindings.items():
            node = _pick_arg(call, spec)
            if node is None:
                continue
            e = self.builder.to_expr(node, frame, d.state)
            if isinstance(e, Unresolved):
                self._unknown(
                    d, e.code, f"{op_id} の引数 {name}: {e.detail}", fn, e.lineno, incomplete=False
                )
                continue
            args[name] = e
        d.events.append(
            Event(
                sequence=len(d.events) + 1,
                kind="call",
                phase="invoked",
                operation=op_id,
                arguments=args,
                source_ref=self._src(fn, call.lineno),
            )
        )
        contract = self.contracts.get(ob.summary_ref or "")
        if contract is not None and contract.may_modify:
            for var in contract.may_modify:
                self._unknown(
                    d,
                    "missing_contract",
                    f"{op_id} が {var} を変更し得るが値は不明",
                    fn,
                    call.lineno,
                )

    def _inline(
        self,
        qual: str,
        call: ast.Call,
        fn: FunctionInfo,
        frame: Frame,
        d: Draft,
        depth: int,
        rest: list[ast.stmt],
        result: list[_Binding] | None = None,
        receiver: LocalValue | None = None,
    ) -> list[Draft] | None:
        """呼出し先の本体を展開する。

        receiver があればメソッドの self に束縛し、実引数は残りの引数に合わせる。
        """
        if any(isinstance(a, ast.Starred) for a in call.args) or any(
            k.arg is None for k in call.keywords
        ):
            self._unknown(
                d,
                "unsupported_syntax",
                f"{qual} の呼出しの実引数の展開（* / **）は対応外",
                fn,
                call.lineno,
            )
            return None
        if depth + 1 > self.limits.depth:
            self._unknown(
                d,
                "limit_depth",
                f"呼出し深さ上限 {self.limits.depth} により {qual} を展開しない",
                fn,
                call.lineno,
            )
            return None
        callee = self.index.function(qual)
        if function_decorators(callee.node):
            self._unknown(
                d, "unsupported_syntax", f"{qual} のデコレーターは対応外", fn, call.lineno
            )
            return None
        cmodule = self.index.module(callee.module)
        assert cmodule is not None
        cframe = Frame(cmodule)
        types = self.builder.types.param_types(callee)
        positional = [a.arg for a in [*callee.node.args.posonlyargs, *callee.node.args.args]]
        params = _params(callee.node)
        if receiver is not None:
            if not positional:
                self._unknown(d, "unsupported_syntax", f"{qual} に self が無い", fn, call.lineno)
                return None
            cframe.bind(positional[0], receiver, types.get(positional[0]))
            params, positional = params[1:], positional[1:]
        for i, p in enumerate(params):
            node = call.args[i] if i < len(positional) and i < len(call.args) else _keyword(call, p)
            if node is None:
                cframe.bind(
                    p, Unresolved("unresolved_attribute", f"引数 {p} が渡されていない", call.lineno)
                )
            elif isinstance(node, ast.Name) and isinstance(frame.locals.get(node.id), RootAlias):
                cframe.bind(p, frame.locals[node.id], types.get(p))
            else:
                cframe.bind(p, self.builder.to_expr(node, frame, d.state), types.get(p))
        sub = Draft(
            list(d.conds),
            d.state,
            list(d.updates),
            list(d.events),
            list(d.unknowns),
            list(d.lines),
            d.unroll_used,
        )
        results = self._explore(list(callee.node.body), callee, cframe, sub, depth + 1)
        ended: list[Draft] = []
        for r in results:
            if r.ended in ("raise", "unresolved"):
                ended.append(r)
                continue
            # return / fallthrough: 呼出し元へ戻り、残りの文を続ける
            cont = Draft(r.conds, r.state, r.updates, r.events, r.unknowns, r.lines, r.unroll_used)
            cont_frame = frame.copy()
            for b in result or []:
                cont_frame.bind(*b)
            ended.extend(self._explore(list(rest), fn, cont_frame, cont, depth))
        return ended

    def _raise(self, stmt: ast.Raise, fn: FunctionInfo, frame: Frame, d: Draft) -> None:
        d.ended = "raise"
        exc = stmt.exc
        if isinstance(exc, ast.Call):
            qual = self.builder.callee_qualname(exc.func, frame) or ast.unparse(exc.func)
            outcome = outcome_of_raise(exc, qual)
            if outcome is None:
                outcome = Outcome(kind="exception", exception_type=qual)
            d.outcome = outcome.model_copy(update={"source_ref": self._src(fn, stmt.lineno)})
            return
        d.outcome = Outcome(
            kind="exception",
            exception_type=ast.unparse(exc) if exc else None,
            source_ref=self._src(fn, stmt.lineno),
        )

    def _return(self, stmt: ast.Return, fn: FunctionInfo, frame: Frame, d: Draft) -> None:
        d.ended = "return"
        src = self._src(fn, stmt.lineno)
        if stmt.value is None or (
            isinstance(stmt.value, ast.Constant) and stmt.value.value is None
        ):
            # `return None` は `return` と同じ（値を返さない）
            d.outcome = Outcome(kind="return", source_ref=src)
            return
        returned = stmt.value
        awaited = isinstance(returned, ast.Await) and isinstance(returned.value, ast.Call)
        if isinstance(returned, ast.Await) and isinstance(returned.value, ast.Call):
            returned = returned.value
        if isinstance(returned, ast.Call):
            cands = self.builder.callee_candidates(returned.func, frame)
            qual = cands[0] if cands else ast.unparse(returned.func)
            outcome = None if awaited else outcome_of_return_call(returned, qual)
            if outcome is not None:
                d.outcome = outcome.model_copy(update={"source_ref": src})
                return
            matched = self._match_operation(cands)
            if len(matched) == 1 and (awaited or not self._is_async(cands)):
                # 契約済み操作の結果を返す（例 return ToolResult.denied(...)）:
                # 呼出しイベントを記録して return
                op_id, ob = next(iter(matched.values()))
                self._emit_operation(op_id, ob, returned, fn, frame, d)
                d.outcome = Outcome(kind="return", source_ref=src)
                return
            if not awaited and self._trivial_construction(returned, frame):
                # 副作用の無い構築を返す（値は持たないが経路は完全）
                d.outcome = Outcome(kind="return", source_ref=src)
                return
            self._unknown(
                d,
                "unmodeled_outcome",
                f"戻り値の呼出し {qual} を応答として認識できない",
                fn,
                stmt.lineno,
            )
            d.outcome = Outcome(kind="unresolved", source_ref=src)
            return
        value = self.builder.to_expr(stmt.value, frame, d.state)
        if (
            isinstance(value, Unresolved)
            and value.analyzed_call
            and isinstance(stmt.value, ast.Name)
        ):
            # サービス層の戻り値（_assign が _call を通して副作用を記録済みの呼出し結果）を
            # **そのまま** 返す場合だけ、値は持たないが経路は完全とみなす。
            # `return x` 以外（`return x == g()` のような複合式）は、to_expr が左辺の
            # Unresolved をそのまま返してフラグが伝播することがあり、右辺の呼出し g() が
            # 解析されないまま「解析済み」扱いになる（codex レビュー指摘）。複合式は
            # 未解析の部分式を含み得るので、下の一般 UNKNOWN 経路に落とす。
            d.outcome = Outcome(kind="return", source_ref=src)
            return
        if isinstance(value, Unresolved):
            self._unknown(d, "unmodeled_outcome", value.detail, fn, value.lineno)
            d.outcome = Outcome(kind="unresolved", source_ref=src)
            return
        d.return_value = value
        d.outcome = Outcome(kind="return", value=value, source_ref=src)

    # ---------------------------------------------------------------- helpers
    def _unknown(
        self,
        d: Draft,
        code: str,
        detail: str,
        fn: FunctionInfo,
        lineno: int,
        incomplete: bool = True,
        may_affect: list[str] | None = None,
    ) -> None:
        d.unknowns.append(
            UnknownCause(
                code=_safe_unknown_code(code),
                detail=detail,
                source_ref=self._src(fn, lineno),
                may_affect=may_affect,
            )
        )
        if not incomplete:
            # 引数の一部が解決できないだけなら経路の完全性には影響しない
            # （engine が引数検査で UNKNOWN にする）
            d.unknowns[-1] = d.unknowns[-1].model_copy(update={"affects": ["arguments_only"]})

    def _src(self, fn: FunctionInfo, lineno: int) -> SourceRef:
        try:
            rel = str(fn.path.relative_to(self.index.root))
        except ValueError:
            rel = str(fn.path)
        return SourceRef(
            snapshot_id=self.snapshot.snapshot_id,
            symbol=fn.qualname,
            path=rel,
            line_start=lineno,
            line_end=lineno,
        )

    def _to_path(self, n: int, d: Draft, fn: FunctionInfo) -> Path:
        completeness_unknowns = [u for u in d.unknowns if "arguments_only" not in u.affects]
        precision = "exact_in_model" if not completeness_unknowns else "incomplete"
        outcome = d.outcome or Outcome(kind="unresolved")
        label = str(outcome.status) if outcome.status is not None else outcome.kind
        return Path(
            path_id=f"p{n}_{label}",
            guard=guard_expr(d.conds),
            state_updates=d.updates,
            events=d.events,
            outcome=outcome,
            precision=precision,
            unknown_causes=d.unknowns,
            source_refs=[
                SourceRef(
                    snapshot_id=self.snapshot.snapshot_id,
                    symbol=fn.qualname,
                    path=self._src(fn, fn.line_start).path,
                    line_start=min(d.lines) if d.lines else fn.line_start,
                    line_end=max(d.lines) if d.lines else fn.line_end,
                )
            ],
        )

    def _empty(
        self,
        behavior_id: str,
        entrypoint: str,
        code: str,
        detail: str,
        fn: FunctionInfo | None = None,
    ) -> CodeIR:
        return CodeIR(
            behavior_id=behavior_id,
            entrypoint=entrypoint,
            snapshot=self.snapshot,
            analyzer_version=ANALYZER_VERSION,
            limits=self.limits,
            paths=[],
            unknown_causes=[
                UnknownCause(
                    code=_safe_unknown_code(code),
                    detail=detail,
                    source_ref=self._src(fn, fn.line_start) if fn else None,
                )
            ],
        )


def _negate(e: Expr) -> Expr:
    from speclino.domain.expr import Not

    if isinstance(e, Not):
        return e.arg
    return Not(op="not", arg=e)


_Binding = tuple[str, LocalValue, str | None]


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """引数名（位置専用・通常・キーワード専用の順）。"""
    a = node.args
    return [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]


def _pick_arg(call: ast.Call, spec: str) -> ast.expr | None:
    kind, _, key = spec.partition(":")
    if kind == "positional":
        i = int(key)
        return call.args[i] if i < len(call.args) else None
    return _keyword(call, key)


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def analyze_repo(
    root: FsPath,
    behavior_id: str,
    domain: DomainSpec,
    bindings: Bindings,
    contracts: dict[str, OperationContract] | None = None,
    limits: AnalysisLimits = DEFAULT_LIMITS,
    snapshot: SnapshotRef | None = None,
    source_roots: tuple[str, ...] = (".", "src"),
    expand_operations: frozenset[str] = frozenset(),
) -> CodeIR:
    index = RepoIndex(root, source_roots)
    return PathAnalyzer(
        index, domain, bindings, contracts, limits, snapshot, expand_operations
    ).analyze(behavior_id)
