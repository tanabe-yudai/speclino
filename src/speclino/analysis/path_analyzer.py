"""path_analyzer（PoC 9 章）。対応構文だけで書かれた入口関数から Code IR を作る。

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
from speclino.analysis.structural import OPAQUE_STMTS, OUTCOME_CHANNEL, RegionScanner
from speclino.analysis.symbolic_state import (
    ExprBuilder,
    Frame,
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
from speclino.frontend.python.index import FunctionInfo, ModuleInfo, RepoIndex, SymbolNotFound
from speclino.frontend.python.subset import Unsupported, function_decorators, unsupported_stmt

ANALYZER_VERSION = "0.1.0"

# 定数回反復として展開できる要素数の上限（S41b）。ネストした展開も含め、1 つの経路（Draft）で
# ここまでに消費した展開回数の累積で数える（Draft.unroll_used。if で分岐した枝ごとに独立に
# 数えるので、無関係な枝の展開が互いの予算を奪わない）。PoC 9.3 の初期上限（経路数 64/256、
# 呼出し深さ 2/4）と同じく保守的な初期値とし、監査イベントの列挙や固定件数のアイテム処理など
# 数件程度の反復は展開できる一方、上限を超えたら展開せず既存の安全網（unsupported_syntax による
# UNKNOWN、展開後に経路数が増え過ぎた場合は limit_paths による UNKNOWN）に委ねる。
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


_BUILTINS_MODULE_NAMES = ("builtins", "__builtins__")


def _module_may_rebind(mod: ModuleInfo, name: str) -> bool:
    """モジュールのどこか（最上位に限らず、if/try/with/for/関数・クラス本体の中も含む）で
    name が再束縛され得るか。分からなければ True（安全側）。

    Python の AST では、代入先・for のターゲット・with の as・タプル/リストへの分解・複合代入・
    海象演算子の代入先は、ネストの深さや構文の種類によらず常に ``ast.Name(ctx=Store)`` になる。
    これを ``ast.walk`` で拾うだけでほとんどの再束縛パターンをまとめて検出できる。加えて
    import 系（相対・ワイルドカード含む）、global/nonlocal 宣言、属性への代入
    （例 ``builtins.range = ...``）、そして ``builtins``/``__builtins__`` への参照が
    1 つでもあれば（``setattr(builtins, "range", ...)`` 等を個別に判定せずまとめて拒否する
    ため）再束縛し得るとみなす。
    """
    for node in ast.walk(mod.tree):
        rebinds = (
            (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store))
            or (isinstance(node, ast.Name) and node.id in _BUILTINS_MODULE_NAMES)
            or (
                isinstance(node, ast.Attribute)
                and node.attr == name
                and isinstance(node.ctx, ast.Store)
            )
            or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node.name == name
            )
            or (
                isinstance(node, ast.Import)
                and any((a.asname or a.name.split(".")[0]) == name for a in node.names)
            )
            or (
                isinstance(node, ast.ImportFrom)
                and (
                    any(a.name == "*" for a in node.names)
                    or any((a.asname or a.name) == name for a in node.names)
                )
            )
            or (isinstance(node, ast.Global | ast.Nonlocal) and name in node.names)
        )
        if rebinds:
            return True
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
        if _module_may_rebind(frame.module, "range"):
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
    ) -> None:
        self.index = index
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
        for arg in fn.node.args.args:
            frame.locals[arg.arg] = RootAlias(arg.arg)
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
                case ast.Expr():
                    pass
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
            frame.locals[name] = Unresolved(
                "unsupported_syntax",
                f"{name} は対応外の領域（行 {u.lineno}）で代入される",
                u.lineno,
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
        if isinstance(target, ast.Name):
            if isinstance(value_node, ast.Call):
                qual = self.builder.callee_qualname(value_node.func, frame)
                pure_var = d.state.pure_call_vars.get(qual or "")
                if pure_var is not None:
                    # 純粋な呼出しの結果は名前付き変数（副作用なし、イベントなし）
                    frame.locals[target.id] = d.state.value(pure_var)
                    return None
                ended = self._call(value_node, fn, frame, d, depth, rest)
                # code は UnknownCode の正当な値にする（_unknown 経由で UnknownCause.code に
                # そのまま渡り得るため。例えばこの変数を後で if の条件に使うと、その時点の
                # cond.code がそのまま _unknown へ渡る）。_return はこの印を「値は追えないが
                # サービス層の戻り値として経路は完全」の判定に使う（下記参照）
                frame.locals[target.id] = Unresolved(
                    "unresolved_call", f"呼出し結果: {ast.unparse(value_node)}", value_node.lineno
                )
                return ended
            value = self.builder.to_expr(value_node, frame, d.state)
            frame.locals[target.id] = value
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
    ) -> list[Draft] | None:
        """呼出し文。経路が終了（呼出し先で raise）したときは終了済み Draft 群を返す。"""
        qual = self.builder.callee_qualname(call.func, frame)
        if qual is None:
            self._unknown(
                d,
                "unresolved_call",
                f"呼出し先を解決できない: {ast.unparse(call.func)}",
                fn,
                call.lineno,
            )
            return None
        if qual in self._op_by_symbol:
            op_id, ob = self._op_by_symbol[qual]
            self._emit_operation(op_id, ob, call, fn, frame, d)
            return None
        if qual in self._known_pure:
            return None
        if self.index.is_function(qual):
            return self._inline(qual, call, fn, frame, d, depth, rest)
        self._unknown(d, "unresolved_call", f"契約の無い外部呼出し: {qual}", fn, call.lineno)
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
    ) -> list[Draft] | None:
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
        params = [a.arg for a in callee.node.args.args]
        for i, p in enumerate(params):
            node = call.args[i] if i < len(call.args) else _keyword(call, p)
            if node is None:
                cframe.locals[p] = Unresolved(
                    "unresolved_attribute", f"引数 {p} が渡されていない", call.lineno
                )
            elif isinstance(node, ast.Name) and isinstance(frame.locals.get(node.id), RootAlias):
                cframe.locals[p] = frame.locals[node.id]
            else:
                cframe.locals[p] = self.builder.to_expr(node, frame, d.state)
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
            ended.extend(self._explore(list(rest), fn, frame.copy(), cont, depth))
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
        if stmt.value is None:
            d.outcome = Outcome(kind="return", source_ref=src)
            return
        if isinstance(stmt.value, ast.Call):
            qual = self.builder.callee_qualname(stmt.value.func, frame) or ast.unparse(
                stmt.value.func
            )
            outcome = outcome_of_return_call(stmt.value, qual)
            if outcome is not None:
                d.outcome = outcome.model_copy(update={"source_ref": src})
                return
            if qual in self._op_by_symbol:
                # 契約済み操作の結果を返す（例 return ToolResult.denied(...)）:
                # 呼出しイベントを記録して return
                op_id, ob = self._op_by_symbol[qual]
                self._emit_operation(op_id, ob, stmt.value, fn, frame, d)
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
        if isinstance(value, Unresolved) and value.code == "unresolved_call":
            # サービス層の戻り値（呼出し結果、_assign が付けた印）。値は持たないが経路は完全
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
) -> CodeIR:
    index = RepoIndex(root, source_roots)
    return PathAnalyzer(index, domain, bindings, contracts, limits, snapshot).analyze(behavior_id)
