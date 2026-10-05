"""遷移（状態遷移図）の義務の検査（設計 5.1）。

宣言した属性への代入ごとに、経路上の前の状態から許された遷移かを Z3 で確かめる。前の状態は
1 つ目の代入が `before.<属性>`（経路条件で絞る）、以降は直前の代入値。
同じ値の再代入は遷移ではないので許す。
宣言した属性だけを見る（open-world）。VIOLATED / VERIFIED_IN_SCOPE は exact_in_model の経路だけ。
"""

from __future__ import annotations

from typing import Any

import z3

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
)
from speclino.conformance.typing import IRTypeError
from speclino.domain.code_ir import CodeIR, Path
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Expr, const, describe_expr
from speclino.domain.finding import Finding
from speclino.domain.uml import StateMachine


class _StateChecker:
    def __init__(
        self, sm: StateMachine, code: CodeIR, config: EngineConfig, domain: DomainSpec, ty: str
    ) -> None:
        self.sm = sm
        self.code = code
        self.config = config
        self.domain = domain
        self.engine = ConformanceEngine(domain, config)
        self.ctx = self.engine.ctx
        self.channel = f"write:{sm.attribute}"
        self.state = {s: self.ctx.const(const(ty, s)) for s in sm.states}
        self.guards = {
            t.id: self.ctx.encode_bool(t.guard) for t in sm.transitions if t.guard is not None
        }
        self.before = self.ctx.variable(f"before.{sm.attribute}", ty)
        self.initial = self.state[sm.initial]
        self.allowed_text = ", ".join(f"{t.source}→{t.target}" for t in sm.transitions)
        self.guard_channels = guard_channels(t.guard for t in sm.transitions if t.guard is not None)

    def touches(self, channel: str) -> bool:
        return channel == self.channel or channel in self.guard_channels

    def allowed(self, prev: Any, new: Any) -> Any:
        parts = [prev == new]  # 同じ値の再代入は遷移ではない
        for t in self.sm.transitions:
            conj = [prev == self.state[t.source], new == self.state[t.target]]
            if t.id in self.guards:
                conj.append(self.guards[t.id])
            parts.append(z3.And(*conj))
        return z3.Or(*parts)

    def writes(self, path: Path) -> list[tuple[int | None, Expr | None, str]]:
        """(sequence, 代入値, certainty)。write イベントが無ければ state_updates を使う。"""
        events: list[tuple[int | None, Expr | None, str]] = [
            (e.sequence, e.value, e.certainty)
            for e in path.events
            if e.kind == "write" and e.target == self.sm.attribute
        ]
        if events:
            return events
        return [
            (None, su.value, "must") for su in path.state_updates if su.target == self.sm.attribute
        ]

    def check_path(self, path: Path) -> ObligationResult | None:
        timeout = self.config.solver_timeout_ms
        in_states = z3.Or(*(self.before == v for v in self.state.values()))
        base = [
            *self.engine.assumptions,
            self.ctx.encode_bool(path.guard),
            *post_state_constraints(self.ctx, self.domain, path),
            in_states,
        ]
        s = self.ctx.solver(timeout)
        s.add(*base)
        r = s.check()
        if r == z3.unknown:
            return solver_limit(f"経路 {path.path_id} の条件", timeout)
        if r == z3.unsat:
            return None  # 状態遷移図の状態にある入力では通らない経路
        blocked = inexact_reason(path, blocking_causes(path.unknown_causes, self.touches))
        if blocked is not None:
            return blocked
        writes = self.writes(path)
        if any(c == "may" for _, _, c in writes):
            return ObligationResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} の {self.sm.attribute} への代入が条件付き（may）",
                next_steps=["条件付きイベントの条件を経路に分割する"],
            )
        if any(v is None for _, v, _ in writes):
            return ObligationResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} の {self.sm.attribute} への代入値が分からない",
            )
        if len(writes) > 1 and self.guards:
            # 遷移のガードは入口の状態（before）で書かれるため、2 回目以降の代入には当てられない
            return ObligationResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} は {self.sm.attribute} に複数回代入し、"
                "図にガードつき遷移がある",
                next_steps=["代入を 1 回にするか、ガードの無い遷移に分ける"],
            )
        expected = f"{self.sm.attribute} の代入が許された遷移（{self.allowed_text}）"
        prev = self.before
        for i, (seq, value, _) in enumerate(writes):
            assert value is not None
            new = self.ctx.encode(value)
            s = self.ctx.solver(timeout)
            s.add(*base, z3.Not(self.allowed(prev, new)))
            if i == 0:
                # 初期状態への代入は生成かもしれないので、ここでは初期状態以外の値だけを判定する
                s.add(new != self.initial)
            r = s.check()
            if r == z3.unknown:
                return solver_limit(f"経路 {path.path_id} の遷移", timeout)
            if r == z3.sat:
                model = s.model()
                src = model.eval(prev, model_completion=True)
                dst = model.eval(new, model_completion=True)
                at = f"seq {seq} の" if seq is not None else ""
                actual = f"{at}代入 {self.sm.attribute} = {describe_expr(value)}（{src} → {dst}）"
                cex = symbolic_counterexample(self.ctx, path, model, actual)
                return ObligationResult(
                    "VIOLATED",
                    "state_mismatch",
                    f"経路 {path.path_id} で状態遷移 {src} → {dst} は図 {self.sm.id} で許されない",
                    expected,
                    actual,
                    cex,
                )
            if i == 0:
                unknown = self._initial_write(path, base, new)
                if unknown is not None:
                    return unknown
            prev = new
        return ObligationResult("VERIFIED_IN_SCOPE", "conformant", "成立", expected)

    def _initial_write(self, path: Path, base: list[Any], new: Any) -> ObligationResult | None:
        """入口の状態から初期状態への代入で、図に無い遷移になり得るなら判定しない。

        生成（オブジェクトを作って初期状態にする）か遷移か、Code IR からは判別できない。
        経路条件の下でありうる前の状態から初期状態への遷移が図にあれば通常どおり成立。
        """
        timeout = self.config.solver_timeout_ms
        s = self.ctx.solver(timeout)
        s.add(*base, new == self.initial, z3.Not(self.allowed(self.before, new)))
        r = s.check()
        if r == z3.unknown:
            return solver_limit(f"経路 {path.path_id} の初期状態への代入", timeout)
        if r == z3.unsat:
            return None
        return ObligationResult(
            "UNKNOWN",
            "unsupported_analysis",
            f"経路 {path.path_id}: 初期状態 {self.sm.initial} への代入。生成か遷移か判別できない",
            next_steps=["生成の経路を別の振る舞いに分けるか、経路条件で前の状態を限定する"],
        )


def check_state_machine(
    sm: StateMachine,
    code: CodeIR,
    config: EngineConfig,
    *,
    domain: DomainSpec | None = None,
) -> list[Finding]:
    """状態遷移図 1 つを Code IR（振る舞い 1 つ）で検査し、Finding を 1 つ返す。

    domain は属性の列挙型と経路条件の型環境（既定は空 → 属性の対応が無いので UNKNOWN）。
    """
    ctx = ObligationContext(
        requirement_id=sm.id,
        behavior_id=code.behavior_id,
        level=sm.level,
        claim_scope="memory_state",
        analysis_scope=scope_text(code),
    )
    dom = domain or DomainSpec()
    ty = dom.type_of(sm.attribute)
    if ty is None or not dom.is_enum(ty):
        return [
            ctx.unknown(
                f"状態の属性 {sm.attribute} に対応する列挙型の変数が見つからない",
                ["domain に属性と列挙型を宣言する（bindings.yaml の対応も確認する）"],
            )
        ]
    missing = sorted(set(sm.states) - set(dom.enums[ty]))
    if missing:
        return [
            ctx.unknown(
                f"図の状態 {', '.join(missing)} が列挙 {ty} に無い（対応が見つからない）",
                ["図の状態名と domain の列挙値を合わせる"],
            )
        ]
    try:
        checker = _StateChecker(sm, code, config, dom, ty)
        return [_check(ctx, checker)]
    except (IRTypeError, ValueError) as e:
        return [
            ctx.unknown(
                f"ガード・経路条件・代入値の型を解決できない（{e}）",
                ["ガードの変数を domain に宣言する"],
            )
        ]


def _check(ctx: ObligationContext, checker: _StateChecker) -> Finding:
    code = checker.code
    blocking = blocking_causes(code.unknown_causes, checker.touches)
    if blocking:
        causes = "; ".join(u.detail for u in blocking)
        return ctx.unknown(
            f"振る舞い全体の未解決要因が {checker.sm.attribute} への代入に触れ得る（{causes}）",
            ["未解決要因を解消するか、解析上限を引き上げて再解析する"],
        )
    if not code.paths:
        return ctx.unknown("解析経路が無い", ["入口の操作と bindings を確認する"])
    # 網羅性: 図の状態にある入力の一部がどの解析経路にも入らなければ判定しない
    s = checker.ctx.solver(checker.config.solver_timeout_ms)
    in_states = z3.Or(*(checker.before == v for v in checker.state.values()))
    guards = [checker.ctx.encode_bool(p.guard) for p in code.paths]
    s.add(*checker.engine.assumptions, in_states, z3.Not(z3.Or(*guards)))
    r = s.check()
    if r != z3.unsat:
        return ctx.unknown(
            "入力の一部がどの解析経路にも対応しない（経路の列挙が不完全）"
            if r == z3.sat
            else "網羅性の判定がソルバー上限に達した",
            ["解析上限を引き上げて再解析する（analyze_focus）"],
        )
    results: list[tuple[Path, ObligationResult]] = []
    for path in code.paths:
        res = checker.check_path(path)
        if res is not None:
            results.append((path, res))
    return ctx.aggregate(results, f" {checker.sm.attribute} の代入が許された遷移")


__all__ = ["check_state_machine"]
