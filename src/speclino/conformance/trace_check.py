"""順序（シーケンス図）と包含（アクティビティ図）の義務の検査（設計 5.1）。

- 順序（contains_subsequence）: ガードが成り立つ経路の呼出し列が、図の呼出し列を部分列として含むか。
  図に無い呼出しが間にあっても違反にしない（open-world、PoC 10.5）
- 包含（included_in）: 経路の呼出しがすべて図のどれかの道筋で消費され、終了に達するか。
  外れたら closed: true の図だけ VIOLATED、それ以外は REVIEW

判定は経路 P ごとに `経路条件 ∧ Cov ∧ ¬OK` の充足性で行う（Cov は図が何かを要求する入力、
OK は P の呼出し列で受理に達した道筋のガード）。
VIOLATED / VERIFIED_IN_SCOPE は exact_in_model の経路だけ。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import z3

from speclino.conformance.automaton import (
    ConfigLimit,
    GuardOracle,
    NormalizedAutomaton,
    SolverLimit,
    accepting_guard_sets,
    disjunction,
    explain_subsequence,
    normalize,
    run_word,
)
from speclino.conformance.engine import (
    ConformanceEngine,
    EngineConfig,
    post_state_constraints,
    symbolic_counterexample,
)
from speclino.conformance.obligation import (
    ObligationContext,
    ObligationResult,
    blocking_causes,
    guard_channels,
    inexact_reason,
    scope_text,
    solver_limit,
    spans_to_refs,
    unbound_guard_variables,
    unbound_unknown,
)
from speclino.conformance.typing import IRTypeError
from speclino.domain.code_ir import CodeIR, Path
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import Category, Finding
from speclino.domain.uml import TraceObligation

ResolveOperation = Callable[[str], str | None]


def _identity(op: str) -> str | None:
    return op


class _TraceChecker:
    def __init__(
        self,
        ob: TraceObligation,
        code: CodeIR,
        config: EngineConfig,
        domain: DomainSpec,
        a: NormalizedAutomaton,
    ) -> None:
        self.ob = ob
        self.code = code
        self.config = config
        self.a = a
        self.domain = domain
        self.engine = ConformanceEngine(domain, config)
        self.ctx = self.engine.ctx
        self.subsequence = ob.mode == "contains_subsequence"
        self.involved = a.operations
        self.guard_channels = guard_channels(a.guards.values())

    # 観測: 順序は図の呼出しだけ、包含は経路のすべての呼出し。ガードが参照する変数・終了結果も
    def touches(self, channel: str) -> bool:
        if channel in self.guard_channels:
            return True
        if not channel.startswith("call:"):
            return False
        return not self.subsequence or channel[len("call:") :] in self.involved

    def oracle(self, base: list[Any]) -> GuardOracle:
        return GuardOracle(self.ctx, base, self.a.guards, self.config.solver_timeout_ms)

    def check_path(self, path: Path) -> ObligationResult | None:
        """経路 1 本の結果。対象外の経路は None。

        順序: 図のガードと両立しない経路は対象外（図はその入力に何も要求しない）。
        包含: 図の範囲外（どの道筋のガードにも入らない）の経路も対象。closed なら違反になり得る。
        """
        timeout = self.config.solver_timeout_ms
        base = [
            *self.engine.assumptions,
            self.ctx.encode_bool(path.guard),
            *post_state_constraints(self.ctx, self.domain, path),
        ]
        s0 = self.ctx.solver(timeout)
        s0.add(*base)
        r0 = s0.check()
        if r0 == z3.unknown:
            return solver_limit(f"経路 {path.path_id} の条件", timeout)
        if r0 == z3.unsat:
            return None
        oracle = self.oracle(base)
        try:
            cov = accepting_guard_sets(self.a, oracle)
        except SolverLimit:
            return solver_limit(f"経路 {path.path_id} と図のガード", timeout)
        if self.subsequence and not cov:
            return None
        blocked = inexact_reason(path, blocking_causes(path.unknown_causes, self.touches))
        if blocked is not None:
            return blocked
        calls = [e for e in path.events if e.kind == "call" and e.operation]
        may = [e for e in calls if e.certainty == "may" and self.touches(f"call:{e.operation}")]
        if may:
            ops = ", ".join(sorted({e.operation or "" for e in may}))
            return ObligationResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} の呼出し {ops} が条件付き（may）のため順序を確定できない",
                next_steps=["条件付きイベントの条件を経路に分割する"],
            )
        word = [e.operation or "" for e in calls]
        try:
            ok, dead = run_word(word, self.a, oracle, skip=self.subsequence)
        except SolverLimit:
            return solver_limit(f"経路 {path.path_id} の模擬", timeout)
        s = self.ctx.solver(timeout)
        s.add(*base, z3.Not(disjunction(oracle, ok)))
        if self.subsequence:
            s.add(disjunction(oracle, cov))
        r = s.check()
        if r == z3.unknown:
            return solver_limit(f"経路 {path.path_id}", timeout)
        expected = self._expected_text()
        if r == z3.unsat:
            return ObligationResult("VERIFIED_IN_SCOPE", "conformant", "成立", expected)
        model = s.model()
        true_guards = {
            gid
            for gid, g in oracle.encoded.items()
            if z3.is_true(model.eval(g, model_completion=True))
        }
        actual = "呼出し列: " + (" → ".join(word) if word else "（呼出しなし）")
        if self.subsequence:
            return self._sequence_violation(path, word, true_guards, model, expected, actual)
        in_range = z3.is_true(model.eval(disjunction(oracle, cov), model_completion=True))
        return self._activity_mismatch(path, calls, dead, in_range, model, expected, actual)

    def _expected_text(self) -> str:
        if self.subsequence:
            return f"{self.ob.entry_operation} の経路が図 {self.ob.id} の呼出し列を部分列として含む"
        return f"{self.ob.entry_operation} の経路の呼出しが図 {self.ob.id} のどれかの道筋に沿う"

    def _sequence_violation(
        self,
        path: Path,
        word: list[str],
        true_guards: set[str],
        model: Any,
        expected: str,
        actual: str,
    ) -> ObligationResult:
        last, nexts = explain_subsequence(word, self.a, true_guards)
        want = " か ".join(nexts) or "図の終了"
        where = f"{last} の後に" if last else "最初に"
        detail = f"{where} {want} の呼出しを期待したが、経路はそれを呼ばずに終わる"
        cex = symbolic_counterexample(self.ctx, path, model, f"{actual}（{detail}）")
        return ObligationResult(
            "VIOLATED",
            "missing_effect",
            f"経路 {path.path_id} で順序の義務 {self.ob.id} に反する: {detail}",
            expected,
            actual,
            cex,
        )

    def _activity_mismatch(
        self,
        path: Path,
        calls: list[Any],
        dead: int | None,
        in_range: bool,
        model: Any,
        expected: str,
        actual: str,
    ) -> ObligationResult:
        category: Category
        if dead is not None:
            e = calls[dead]
            detail = f"seq {e.sequence} の呼出し {e.operation} は図のどの道筋にも無い"
            category = "forbidden_effect" if self.ob.closed else "undocumented_behavior"
        elif not in_range:
            detail = "この入力は図のどの道筋のガードにも入らない（図の範囲外の処理）"
            category = "forbidden_effect" if self.ob.closed else "undocumented_behavior"
        else:
            detail = "経路の呼出し列が、この入力で図の道筋に沿って終了に達しない"
            category = "missing_effect" if self.ob.closed else "potential_violation"
        cex = symbolic_counterexample(self.ctx, path, model, f"{actual}（{detail}）")
        if self.ob.closed:
            return ObligationResult(
                "VIOLATED",
                category,
                f"経路 {path.path_id} が閉じたアクティビティ図 {self.ob.id} に反する: {detail}",
                expected,
                actual,
                cex,
            )
        return ObligationResult(
            "REVIEW",
            category,
            f"経路 {path.path_id} に図 {self.ob.id} に無い処理がある: {detail}"
            "（図が closed でないため違反とは確定しない）",
            expected,
            actual,
            cex,
            next_steps=["図に処理を追加するか、図を closed: true にして網羅を宣言する"],
        )


def check_trace(
    ob: TraceObligation,
    code: CodeIR,
    config: EngineConfig,
    *,
    domain: DomainSpec | None = None,
    resolve_operation: ResolveOperation = _identity,
    known_operations: frozenset[str] | None = None,
    bound_variables: frozenset[str] | None = None,
) -> list[Finding]:
    """順序・包含の義務 1 つを Code IR（入口の振る舞い 1 つ）で検査し、Finding を 1 つ返す。

    domain はガードと経路条件の型環境（既定は空 → 変数を参照するガードは UNKNOWN）。
    resolve_operation は図の `<Class>.<method>` を Code IR の業務操作 ID に写す（None は対応なし）。
    known_operations を渡すと、写した先がその集合（Code IR / contracts に現れる操作 ID）に無いものも
    対応なし → UNKNOWN にする。
    bound_variables を渡すと、ガードがそれ以外の変数を参照するとき UNKNOWN にする。
    """
    ctx = ObligationContext(
        requirement_id=ob.id,
        behavior_id=code.behavior_id,
        level=ob.level,
        claim_scope="command_invocation",
        analysis_scope=scope_text(code),
        spec_refs=tuple(spans_to_refs(ob.spans, ob.id)),
    )
    free = unbound_guard_variables(
        [t.guard for t in ob.automaton.transitions if t.guard is not None], bound_variables
    )
    if free:
        return [unbound_unknown(ctx, free, code.behavior_id)]
    a, unbound = normalize(ob.automaton, resolve_operation, ob.entry_operation, known_operations)
    if unbound:
        return [
            ctx.unknown(
                f"図の要素に対応する操作が見つからない: {', '.join(unbound)}",
                ["名前を Code IR の操作に合わせるか、bindings.yaml に対応を書く"],
            )
        ]
    if not code.paths:
        return [ctx.unknown("入口の操作に解析経路が無い", ["入口の操作と bindings を確認する"])]
    checker = _TraceChecker(ob, code, config, domain or DomainSpec(), a)
    try:
        return [_check(ctx, checker)]
    except (IRTypeError, ValueError) as e:
        return [
            ctx.unknown(
                f"ガードまたは経路条件の型を解決できない（{e}）",
                ["ガードの変数を domain に宣言する"],
            )
        ]
    except ConfigLimit as e:
        return [ctx.unknown(f"図の模擬が上限に達した（{e}）")]


def _check(ctx: ObligationContext, checker: _TraceChecker) -> Finding:
    code, timeout = checker.code, checker.config.solver_timeout_ms
    blocking = blocking_causes(code.unknown_causes, checker.touches)
    if blocking:
        causes = "; ".join(u.detail for u in blocking)
        return ctx.unknown(
            f"振る舞い全体の未解決要因が呼出しの観測に触れ得る（{causes}）",
            ["未解決要因を解消するか、解析上限を引き上げて再解析する"],
        )
    oracle = checker.oracle(list(checker.engine.assumptions))
    try:
        cov = accepting_guard_sets(checker.a, oracle)
    except SolverLimit:
        return ctx.finding(solver_limit("図のガード", timeout))
    if not cov:
        return ctx.finding(
            ObligationResult(
                "REVIEW",
                "vacuous_applicability",
                "図のガードを満たして終了に達する入力が存在しない。図とドメインを確認する",
                next_steps=["図のガードと domain.assumptions の矛盾を確認する"],
            )
        )
    # 網羅性: 図が要求する入力の一部がどの解析経路にも入らなければ（経路上限の超過など）判定しない
    s = checker.ctx.solver(timeout)
    guards = [checker.ctx.encode_bool(p.guard) for p in code.paths]
    s.add(*checker.engine.assumptions, disjunction(oracle, cov), z3.Not(z3.Or(*guards)))
    r = s.check()
    if r != z3.unsat:
        return ctx.unknown(
            "図のガードを満たす入力の一部がどの解析経路にも対応しない（経路の列挙が不完全）"
            if r == z3.sat
            else "網羅性の判定がソルバー上限に達した",
            ["解析上限を引き上げて再解析する（analyze_focus）"],
        )
    results: list[tuple[Path, ObligationResult]] = []
    for path in code.paths:
        res = checker.check_path(path)
        if res is not None:
            results.append((path, res))
    verified = "図の呼出し列を部分列として含む" if checker.subsequence else "呼出しが図の道筋に沿う"
    return ctx.aggregate(results, verified)


__all__ = ["ResolveOperation", "check_trace"]
