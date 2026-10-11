"""UML の義務（順序・包含・遷移・構造）の Finding を作る共通部品（設計 5.1）。

判定ロジックは trace_check / state_check / analysis.structure_check。ここは結果の形と集約だけ。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from speclino.conformance.reasons import category_rank, finding_id, worst
from speclino.domain.code_ir import CodeIR, Path, UnknownCause
from speclino.domain.common import ClaimScope, SourceRef
from speclino.domain.expr import Expr, refs_in
from speclino.domain.finding import Category, Counterexample, Finding, Severity, Verdict
from speclino.domain.spec_ir import RuleLevel
from speclino.domain.uml import SourceSpan


@dataclass
class ObligationResult:
    verdict: Verdict
    category: Category
    message: str
    expected: str | None = None
    actual: str | None = None
    counterexample: Counterexample | None = None
    next_steps: list[str] = field(default_factory=list)


def severity(level: RuleLevel, verdict: Verdict) -> Severity:
    if verdict == "VERIFIED_IN_SCOPE":
        return "info"
    if verdict == "VIOLATED":
        return "high" if level == "mandatory" else "medium"
    return "medium" if level == "mandatory" else "low"


def spans_to_refs(spans: Iterable[SourceSpan], requirement_id: str) -> list[SourceRef]:
    return [
        SourceRef(
            snapshot_id=s.diagram_id,
            requirement_id=requirement_id,
            line_start=s.line,
            line_end=s.line,
        )
        for s in spans
    ]


def scope_text(code: CodeIR) -> str:
    lim = code.limits
    exact = sum(1 for p in code.paths if p.precision == "exact_in_model" and not p.unknown_causes)
    return (
        f"{len(code.paths)} 経路（うち完全 {exact}、呼出し深さ {lim.depth}、"
        f"経路上限 {lim.max_paths}、"
        f"ソルバー {lim.solver_timeout_ms}ms、解析器 {code.analyzer_version}）"
        "。前提: 型の注釈は実行時の値と整合する（mypy strict）、属性の読み出しは純粋、"
        "await の間に他のタスクが入口の引数の属性を書き換えない"
    )


def blocking_causes(
    causes: Iterable[UnknownCause], touches: Callable[[str], bool]
) -> list[UnknownCause]:
    """判定を妨げる未解決要因。触れ得る観測が分からない（None）要因は常に妨げる。"""
    out: list[UnknownCause] = []
    for u in causes:
        if u.affects == ["arguments_only"]:
            continue
        if u.may_affect is None or any(touches(ch) for ch in u.may_affect):
            out.append(u)
    return out


def guard_channels(guards: Iterable[Expr]) -> set[str]:
    """ガードが参照する観測（before./after.x → write:x、outcome.* → outcome）。"""
    out: set[str] = set()
    for g in guards:
        for r in refs_in(g):
            out.add("outcome" if r.namespace == "outcome" else f"write:{r.variable}")
    return out


def unbound_guard_variables(guards: Iterable[Expr], bound: frozenset[str] | None) -> list[str]:
    """ガードが参照する変数（before./after.）のうち、コードの値に対応づいていないもの。

    対応の無い変数は経路条件と無関係な自由変数になり、「コードがその条件で分岐しない」と
    読めてしまう（偽の VIOLATED / VERIFIED）。bound が None なら検査しない（呼び出し側が
    対応を持たないとき）。
    """
    if bound is None:
        return []
    return sorted(
        {r.variable for g in guards for r in refs_in(g) if r.namespace != "outcome"} - bound
    )


def unbound_unknown(ctx: ObligationContext, names: list[str], behavior_id: str) -> Finding:
    return ctx.unknown(
        f"ガードの変数 {', '.join(names)} が振る舞い {behavior_id} のコードの値に対応していない",
        [f"bindings.yaml の behaviors.{behavior_id}.variables に図の変数名で対応を書く"],
    )


@dataclass(frozen=True)
class ObligationContext:
    requirement_id: str
    behavior_id: str
    level: RuleLevel
    claim_scope: ClaimScope
    analysis_scope: str | None = None
    spec_refs: tuple[SourceRef, ...] = ()

    def finding(
        self,
        res: ObligationResult,
        path_id: str | None = None,
        code_refs: Iterable[SourceRef] = (),
        evidence_refs: Iterable[str] | None = None,
    ) -> Finding:
        return Finding(
            id=finding_id(self.requirement_id, self.behavior_id, path_id, res.category),
            requirement_id=self.requirement_id,
            behavior_id=self.behavior_id,
            verdict=res.verdict,
            category=res.category,
            severity=severity(self.level, res.verdict),
            message=res.message,
            expected=res.expected,
            actual=res.actual,
            counterexample=res.counterexample,
            evidence_refs=list(evidence_refs)
            if evidence_refs is not None
            else ([path_id] if path_id else []),
            claim_scope=self.claim_scope,
            analysis_scope=self.analysis_scope,
            code_refs=list(code_refs),
            spec_refs=list(self.spec_refs),
            next_steps=res.next_steps,
        )

    def unknown(self, message: str, next_steps: list[str] | None = None) -> Finding:
        return self.finding(
            ObligationResult(
                "UNKNOWN", "unsupported_analysis", message, next_steps=next_steps or []
            )
        )

    def aggregate(
        self, results: list[tuple[Path, ObligationResult]], verified_message: str
    ) -> Finding:
        """経路ごとの結果を集約する（VIOLATED > REVIEW > UNKNOWN > VERIFIED）。"""
        if not results:
            return self.unknown(
                "図のガードと両立する解析経路が無い",
                ["入口の操作と Code IR の対応（bindings）を確認する"],
            )
        verdict = worst([r.verdict for _, r in results])
        path, res = min(
            ((p, r) for p, r in results if r.verdict == verdict),
            key=lambda pr: (category_rank(pr[1].category), pr[0].path_id),
        )
        if verdict == "VERIFIED_IN_SCOPE":
            return self.finding(
                ObligationResult(
                    "VERIFIED_IN_SCOPE",
                    "conformant",
                    f"関連する {len(results)} 経路すべてで{verified_message}（対応モデル内）",
                    expected=res.expected,
                ),
                code_refs=[sr for p, _ in results for sr in p.source_refs],
                evidence_refs=[p.path_id for p, _ in results],
            )
        return self.finding(res, path.path_id, path.source_refs)


def solver_limit(what: str, timeout_ms: int) -> ObligationResult:
    return ObligationResult(
        "UNKNOWN",
        "solver_limit",
        f"{what}の判定がソルバー上限（{timeout_ms}ms）に達した",
        next_steps=["ソルバー上限を引き上げて再解析する（analyze_focus）"],
    )


def inexact_reason(path: Path, blocking: list[UnknownCause]) -> ObligationResult | None:
    """不変条件: 判定してよいのは exact_in_model で、関係する観測を妨げる要因が無い経路だけ。"""
    if path.precision != "exact_in_model":
        causes = "; ".join(u.detail for u in path.unknown_causes) or path.precision
        return ObligationResult(
            "UNKNOWN",
            "unsupported_analysis",
            f"経路 {path.path_id} は解析の精度が {path.precision} のため判定しない（{causes}）",
            next_steps=["対象関数を対応構文だけで書き直すか、解析上限を引き上げる"],
        )
    if blocking:
        causes = "; ".join(u.detail for u in blocking)
        return ObligationResult(
            "UNKNOWN",
            "unsupported_analysis",
            f"経路 {path.path_id} の未解決要因が検査する観測に触れ得る（{causes}）",
            next_steps=["未解決要因を解消して経路を完全にする"],
        )
    return None
