"""path_analyzer（PoC 9 章）。対応構文だけで書かれた入口関数から Code IR を作る。

経路を分岐させながら記号的状態を更新し、guard / state_updates / events / outcome を持つ
Path を列挙する。早期 return より後を実行済みにしない。呼出しは bindings.operations の
契約、リポジトリ内関数のローカル要約（深さ上限）、それ以外は未解決として記録する。
対象リポジトリのモジュールを import しない。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path as FsPath

from speclino.analysis.limits import DEFAULT_LIMITS
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
)
from speclino.domain.common import SnapshotRef, SourceRef
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Expr
from speclino.frontend.python.adapters.fastapi_outcome import (
    outcome_of_raise,
    outcome_of_return_call,
)
from speclino.frontend.python.index import FunctionInfo, RepoIndex, SymbolNotFound
from speclino.frontend.python.subset import function_decorators, unsupported_stmt

ANALYZER_VERSION = "0.1.0"


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
        )


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
            d.lines.append(stmt.lineno)
            if (u := unsupported_stmt(stmt)) is not None:
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
                frame.locals[target.id] = Unresolved(
                    "call_result", f"呼出し結果: {ast.unparse(value_node)}", value_node.lineno
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
            list(d.conds), d.state, list(d.updates), list(d.events), list(d.unknowns), list(d.lines)
        )
        results = self._explore(list(callee.node.body), callee, cframe, sub, depth + 1)
        ended: list[Draft] = []
        for r in results:
            if r.ended in ("raise", "unresolved"):
                ended.append(r)
                continue
            # return / fallthrough: 呼出し元へ戻り、残りの文を続ける
            cont = Draft(r.conds, r.state, r.updates, r.events, r.unknowns, r.lines)
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
        if isinstance(value, Unresolved) and value.code == "call_result":
            # サービス層の戻り値（呼出し結果）。値は持たないが経路は完全
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
    ) -> None:
        d.unknowns.append(
            UnknownCause(code=code, detail=detail, source_ref=self._src(fn, lineno))  # type: ignore[arg-type,unused-ignore]
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
                UnknownCause(  # type: ignore[arg-type,unused-ignore]
                    code=code,
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
