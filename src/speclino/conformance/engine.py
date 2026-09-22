"""conformance_engine（PoC 10 章）。Spec IR と Code IR から Finding を決定的に導く。

各 Rule × Path について
    入口仮定 ∧ ドメイン ∧ 要求の適用条件 ∧ 実装経路の条件 ∧ 経路の終了状態 ∧ ¬保証
の充足性を Z3 で判定する。SAT なら反例、全経路 UNSAT なら VERIFIED_IN_SCOPE、
ソルバーの UNKNOWN / timeout は違反なしに読み替えず UNKNOWN にする。
LLM・adapters に依存しない（テストで import を禁止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import z3

from speclino.conformance.encode import Z3Context
from speclino.conformance.reasons import (
    category_rank,
    count_category,
    finding_id,
    outcome_category,
    severity_for,
    worst,
)
from speclino.conformance.typing import (
    OUTCOME_KIND_ENUM,
    IRTypeError,
    TypeEnv,
    infer_type,
    require_boolean,
)
from speclino.domain.code_ir import CodeIR, Event, Path, UnknownCause
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import BOOL_TYPE, Binary, Const, Expr, Ref, const, refs_in
from speclino.domain.finding import (
    Category,
    Counterexample,
    CoverageCounts,
    Finding,
    Findings,
    Verdict,
    VerdictCounts,
)
from speclino.domain.spec_ir import EventRequirement, ExpressionAssertion, Rule, SpecIR

ENGINE_VERSION = "0.1.0"


class EngineInputError(ValueError):
    """IR が型検査を通らない等、検査を開始できない入力不正（CLI では終了コード 2）。"""


@dataclass(frozen=True)
class EngineConfig:
    solver_timeout_ms: int = 500


@dataclass
class _PathResult:
    verdict: Verdict
    category: Category
    message: str
    expected: str | None = None
    actual: str | None = None
    counterexample: Counterexample | None = None
    next_steps: list[str] = field(default_factory=list)


@dataclass
class _Selection:
    """event_requirement で選んだイベントと回数の範囲。"""

    must: list[Event]
    may: list[Event]

    @property
    def min_count(self) -> int:
        return len(self.must)

    @property
    def max_count(self) -> int:
        return len(self.must) + len(self.may)


def _solver(ctx: Z3Context, timeout_ms: int) -> Any:
    return ctx.solver(timeout_ms)


def _check(s: Any) -> str:
    r = s.check()
    if r == z3.sat:
        return "sat"
    if r == z3.unsat:
        return "unsat"
    return "unknown"


def _channel_of(req: EventRequirement) -> str:
    """要求が観測するチャネル（analysis.structural と同じ語彙）。"""
    if req.event_kind == "call":
        return f"call:{req.operation_or_target}"
    if req.event_kind == "write":
        return f"write:{req.operation_or_target}"
    return "outcome"


def _count_ok(op: str, required: int, n: int) -> bool:
    return {"eq": n == required, "ge": n >= required, "le": n <= required}[op]


class ConformanceEngine:
    def __init__(self, domain: DomainSpec, config: EngineConfig | None = None) -> None:
        self.domain = domain
        self.config = config or EngineConfig()
        self.env = TypeEnv(domain)
        self.ctx = Z3Context(self.env)
        try:
            self.assumptions = [self.ctx.encode_bool(a) for a in domain.assumptions]
        except IRTypeError as e:
            raise EngineInputError(f"domain.assumptions: {e}") from e

    # ------------------------------------------------------------------ public
    def evaluate(self, spec: SpecIR, code: CodeIR, run_id: str | None = None) -> Findings:
        if spec.behavior_id != code.behavior_id:
            raise EngineInputError(
                f"behavior_id が一致しない: spec={spec.behavior_id} code={code.behavior_id}"
            )
        findings = [self.evaluate_rule(rule, spec, code) for rule in spec.rules]
        undocumented = self._undocumented_behavior(spec, code)
        if undocumented is not None:
            findings.append(undocumented)
        counts = VerdictCounts()
        for f in findings:
            counts = counts.model_copy(update={f.verdict: getattr(counts, f.verdict) + 1})
        approved = sum(1 for r in spec.rules if self._approved(r, spec))
        decided = sum(1 for f in findings if f.verdict in ("VERIFIED_IN_SCOPE", "VIOLATED"))
        coverage = CoverageCounts(
            requirements_total=len(spec.rules) + len(spec.unextracted),
            requirements_approved=approved,
            requirements_in_scope=len(spec.rules),
            requirements_decided=decided,
            requirements_unanalyzed=len(spec.unextracted),
        )
        return Findings(run_id=run_id, findings=findings, counts=counts, coverage=coverage)

    def evaluate_rule(self, rule: Rule, spec: SpecIR, code: CodeIR) -> Finding:
        base_kwargs: dict[str, Any] = {
            "requirement_id": rule.id,
            "behavior_id": rule.behavior_id,
            "claim_scope": rule.claim_scope,
            "spec_refs": rule.source_refs,
            "assumptions": [self._describe_expr(a) for a in self.domain.assumptions],
            "analysis_scope": self._scope_text(code),
        }

        def make(res: _PathResult, path_id: str | None, code_refs: list[Any]) -> Finding:
            return Finding(
                id=finding_id(rule.id, rule.behavior_id, path_id, res.category),
                verdict=res.verdict,
                category=res.category,
                severity=severity_for(rule, res.verdict),
                message=res.message,
                expected=res.expected,
                actual=res.actual,
                counterexample=res.counterexample,
                evidence_refs=[path_id] if path_id else [],
                code_refs=code_refs,
                next_steps=res.next_steps,
                **base_kwargs,
            )

        # 0. 承認
        if not self._approved(rule, spec):
            return make(
                _PathResult(
                    "REVIEW",
                    "stale_approval",
                    "この要求の解釈は承認されていない（approval_ref が無いか承認記録に無い）",
                    next_steps=["仕様解釈を確認し承認記録を追加する"],
                ),
                None,
                [],
            )
        # 1. 型検査
        try:
            require_boolean(self.env, rule.applicability, "applicability")
            self._typecheck_assertion(rule)
        except IRTypeError as e:
            raise EngineInputError(f"{rule.id}: {e}") from e
        for rf in refs_in(rule.applicability):
            if rf.namespace != "before":
                raise EngineInputError(f"{rule.id}: applicability は before だけを参照できる")

        applicability = self.ctx.encode_bool(rule.applicability)
        # 2. 空虚な適用条件
        s = _solver(self.ctx, self.config.solver_timeout_ms)
        s.add(*self.assumptions, applicability)
        r = _check(s)
        if r == "unsat":
            return make(
                _PathResult(
                    "REVIEW",
                    "vacuous_applicability",
                    "適用条件を満たす入力が存在しない。空虚な成功にせず仕様/ドメインを確認する",
                    next_steps=["applicability と domain.assumptions の矛盾を確認する"],
                ),
                None,
                [],
            )
        if r == "unknown":
            return make(self._solver_limit("適用条件の充足性"), None, [])

        # 3. 関連経路の抽出
        relevant: list[Path] = []
        for path in code.paths:
            s = _solver(self.ctx, self.config.solver_timeout_ms)
            s.add(*self.assumptions, applicability, self.ctx.encode_bool(path.guard))
            r = _check(s)
            if r == "unknown":
                return make(self._solver_limit(f"経路 {path.path_id} の関連性"), path.path_id, [])
            if r == "sat":
                relevant.append(path)

        # 4. 網羅性: 適用条件を満たすのに、どの経路にも入らない入力があれば未解析
        s = _solver(self.ctx, self.config.solver_timeout_ms)
        guards = [self.ctx.encode_bool(p.guard) for p in code.paths]
        s.add(*self.assumptions, applicability)
        s.add(z3.Not(z3.Or(*guards)) if guards else self.ctx.true())
        r = _check(s)
        if r != "unsat":
            causes = "; ".join(u.detail for u in code.unknown_causes) or "経路の列挙が不完全"
            return make(
                _PathResult(
                    "UNKNOWN",
                    "unsupported_analysis",
                    f"適用条件を満たす入力の一部がどの解析経路にも対応しない（{causes}）"
                    if r == "sat"
                    else "網羅性の判定がソルバー上限に達した",
                    next_steps=self._next_steps_for_unknown(code),
                ),
                None,
                [],
            )

        # 5. 経路ごとの検査
        results: list[tuple[Path, _PathResult]] = []
        for path in relevant:
            results.append((path, self._check_path(rule, path, applicability, code)))

        # 6. 集約（VIOLATED > REVIEW > UNKNOWN > VERIFIED）
        if not results:
            return make(
                _PathResult(
                    "UNKNOWN",
                    "unsupported_analysis",
                    "適用条件に関連する経路が無い",
                    next_steps=self._next_steps_for_unknown(code),
                ),
                None,
                [],
            )
        verdict = worst([res.verdict for _, res in results])
        # 同じ verdict の経路が複数なら、より本質的な category を代表にする
        # （例: 拒否すべき入力が成功する constraint_mismatch を outcome_mismatch より優先）
        path, res = min(
            ((p, r_) for p, r_ in results if r_.verdict == verdict),
            key=lambda pr: category_rank(pr[1].category),
        )
        if verdict == "VERIFIED_IN_SCOPE":
            res = _PathResult(
                "VERIFIED_IN_SCOPE",
                "conformant",
                f"関連する {len(results)} 経路すべてで要求が成立（対応モデル内）",
                expected=res.expected,
            )
            return make(res, None, [sr for p, _ in results for sr in p.source_refs])
        return make(res, path.path_id, list(path.source_refs))

    # ---------------------------------------------------------------- internals
    def _undocumented_behavior(self, spec: SpecIR, code: CodeIR) -> Finding | None:
        """仕様に無い追加処理（open-world、PoC 10.5）: どの要求にも現れない操作の呼出しは REVIEW。

        禁止と明記されていれば count 0 の要求が VIOLATED を出すので、ここでは扱わない。
        """
        referenced = {
            a.operation_or_target
            for r in spec.rules
            if isinstance((a := r.assertion), EventRequirement)
        }
        extra: dict[str, list[str]] = {}
        for path in code.paths:
            for e in path.events:
                if e.kind == "call" and e.operation and e.operation not in referenced:
                    extra.setdefault(e.operation, []).append(path.path_id)
        if not extra:
            return None
        desc = "; ".join(
            f"{op}（経路 {', '.join(sorted(set(ps)))}）" for op, ps in sorted(extra.items())
        )
        return Finding(
            id=finding_id(
                f"{spec.behavior_id}:undocumented", spec.behavior_id, None, "undocumented_behavior"
            ),
            requirement_id=f"{spec.behavior_id}:undocumented",
            behavior_id=spec.behavior_id,
            verdict="REVIEW",
            category="undocumented_behavior",
            severity="medium",
            message=f"仕様に記載の無い操作を呼んでいる: {desc}",
            claim_scope="command_invocation",
            analysis_scope=self._scope_text(code),
            code_refs=[sr for p in code.paths for sr in p.source_refs][:1],
            next_steps=[
                "仕様（Spec IR）にこの操作の要求を追加するか、禁止なら回数 0 の要求を明示する",
            ],
        )

    def _approved(self, rule: Rule, spec: SpecIR) -> bool:
        if rule.approval_ref is None:
            return False
        return any(a.id == rule.approval_ref for a in spec.approvals)

    def _typecheck_assertion(self, rule: Rule) -> None:
        a = rule.assertion
        if isinstance(a, ExpressionAssertion):
            require_boolean(self.env, a.expr, "assertion.expr")
        else:
            for name, e in a.arguments_equal.items():
                infer_type(self.env, e)
                if any(r.namespace != "before" for r in refs_in(e)):
                    raise IRTypeError(f"arguments_equal[{name}] は before だけを参照できる")

    def _solver_limit(self, what: str) -> _PathResult:
        return _PathResult(
            "UNKNOWN",
            "solver_limit",
            f"{what}の判定がソルバー上限（{self.config.solver_timeout_ms}ms）に達した",
            next_steps=["ソルバー上限を引き上げて再解析する（analyze_focus）"],
        )

    def _scope_text(self, code: CodeIR) -> str:
        lim = code.limits
        return (
            f"{len(code.paths)} 経路（呼出し深さ {lim.depth}、経路上限 {lim.max_paths}、"
            f"ソルバー {lim.solver_timeout_ms}ms、解析器 {code.analyzer_version}）"
        )

    def _next_steps_for_unknown(self, code: CodeIR) -> list[str]:
        steps = ["対象関数を対応構文（PoC 3.2）だけで書き直す"]
        codes = {u.code for u in code.unknown_causes} | {
            u.code for p in code.paths for u in p.unknown_causes
        }
        if codes & {"limit_depth", "limit_paths", "limit_time"}:
            steps.insert(0, "解析上限を引き上げて再解析する（analyze_focus）")
        if codes & {"unresolved_call", "missing_contract"}:
            steps.insert(0, "未解決の呼出しに契約（contracts/）を登録する")
        return steps

    def _post_state(self, path: Path) -> list[Any]:
        """経路の終了状態: after.x = 最後の更新値（無ければ before.x）、outcome。"""
        cs: list[Any] = []
        updated: dict[str, Expr] = {}
        for su in path.state_updates:
            updated[su.target] = su.value
        for var, ty in self.domain.variables.items():
            after = self.ctx.variable(f"after.{var}", ty)
            if var in updated:
                cs.append(after == self.ctx.encode(updated[var]))
            else:
                cs.append(after == self.ctx.variable(f"before.{var}", ty))
        kind_var = self.ctx.variable("outcome.kind", OUTCOME_KIND_ENUM)
        cs.append(kind_var == self.ctx.const(const(OUTCOME_KIND_ENUM, path.outcome.kind)))
        if path.outcome.status is not None:
            cs.append(self.ctx.variable("outcome.status", "integer") == path.outcome.status)
        return cs

    def _select(self, req: EventRequirement, path: Path) -> _Selection:
        must: list[Event] = []
        may: list[Event] = []
        for e in path.events:
            if (
                e.kind == req.event_kind
                and e.phase == req.phase
                and e.operation_or_target == req.operation_or_target
            ):
                (must if e.certainty == "must" else may).append(e)
        return _Selection(must, may)

    def _check_path(self, rule: Rule, path: Path, applicability: Any, code: CodeIR) -> _PathResult:
        base = [*self.assumptions, applicability, self.ctx.encode_bool(path.guard)]
        post = self._post_state(path)
        a = rule.assertion
        if isinstance(a, EventRequirement):
            return self._check_event_requirement(rule, a, path, base, post)
        return self._check_expression(rule, a, path, base, post)

    # -- expression ---------------------------------------------------------
    def _check_expression(
        self, rule: Rule, a: ExpressionAssertion, path: Path, base: list[Any], post: list[Any]
    ) -> _PathResult:
        holds = self.ctx.encode_bool(a.expr)
        violates = z3.Not(holds) if rule.modality == "must" else holds
        s = _solver(self.ctx, self.config.solver_timeout_ms)
        s.add(*base, *post, violates)
        r = _check(s)
        expected = self._describe_expr(a.expr) + (
            "" if rule.modality == "must" else " が成立しない"
        )
        if r == "unknown":
            return self._solver_limit(f"経路 {path.path_id}")
        if r == "unsat":
            return self._holds(path, expected, channel=None)
        model = s.model()
        category = self._expression_category(a.expr, path)
        actual = self._describe_outcome_or_state(a.expr, path)
        return self._violation(rule, path, category, expected, actual, model, channel=None)

    def _expression_category(self, expr: Expr, path: Path) -> Category:
        refs = refs_in(expr)
        if any(r.namespace == "after" for r in refs):
            return "state_mismatch"
        if any(r.path == "outcome.status" for r in refs):
            expected_status = self._literal_status(expr)
            return outcome_category(expected_status, path.outcome.status)
        return "constraint_mismatch"

    @staticmethod
    def _literal_status(expr: Expr) -> int | None:
        if isinstance(expr, Binary) and expr.op == "eq":
            for side in (expr.left, expr.right):
                if (
                    isinstance(side, Const)
                    and side.type == "integer"
                    and isinstance(side.value, int)
                ):
                    return side.value
        return None

    def _describe_outcome_or_state(self, expr: Expr, path: Path) -> str:
        parts: list[str] = []
        for r in refs_in(expr):
            if r.namespace == "outcome":
                if r.variable == "status":
                    parts.append(f"outcome.status = {path.outcome.status}")
                else:
                    parts.append(f"outcome.kind = {path.outcome.kind}")
            elif r.namespace == "after":
                upd = [su for su in path.state_updates if su.target == r.variable]
                val = (
                    self._describe_expr(upd[-1].value)
                    if upd
                    else f"before.{r.variable}（更新なし）"
                )
                parts.append(f"after.{r.variable} = {val}")
        return "; ".join(dict.fromkeys(parts)) or "（経路の終了状態）"

    # -- event_requirement --------------------------------------------------
    def _check_event_requirement(
        self, rule: Rule, req: EventRequirement, path: Path, base: list[Any], post: list[Any]
    ) -> _PathResult:
        sel = self._select(req, path)
        channel = _channel_of(req)
        op, required = req.count.operator, req.count.value
        expected = f"{req.operation_or_target} を {req.count.operator} {required} 回" + (
            f"（引数 {', '.join(req.arguments_equal)} が一致）" if req.arguments_equal else ""
        )
        counts_ok = [_count_ok(op, required, n) for n in range(sel.min_count, sel.max_count + 1)]
        count_holds = all(counts_ok)
        count_fails = not any(counts_ok)
        actual = f"{req.operation_or_target} を {sel.min_count} 回" + (
            f"（条件付きで最大 {sel.max_count} 回）" if sel.may else ""
        )

        if rule.modality == "must_not":
            # must_not: 保証全体が偽であることを要求する。回数条件が真になり得るなら違反候補
            if count_fails:
                return self._holds(path, expected + " が成立しない", channel=channel)
            if count_holds and not req.arguments_equal:
                return self._violation(
                    rule, path, "forbidden_effect", expected, actual, None, channel=channel
                )
            return _PathResult(
                "REVIEW",
                "potential_violation",
                "must_not の条件付き成立（引数または may イベント）は人の確認が必要",
            )

        if count_fails:
            category = count_category(op, required, sel.min_count)
            # 不在（回数不足）の根拠は経路のイベント抽出が完了している場合に限る（PoC 6.7）
            if sel.min_count < required and self._blocking_unknowns(path, channel):
                return _PathResult(
                    "UNKNOWN",
                    "unsupported_analysis",
                    f"経路 {path.path_id} のイベント抽出が不完全なため"
                    "「呼出しなし」を根拠にできない",
                    expected,
                    actual,
                    next_steps=["未解決要因を解消して経路を完全にする"],
                )
            return self._violation(rule, path, category, expected, actual, None, channel=channel)
        if not count_holds:
            return _PathResult(
                "REVIEW",
                "potential_violation",
                f"条件付き（may）イベントにより回数が {sel.min_count}〜{sel.max_count} で不定",
                expected,
                actual,
                next_steps=["条件付きイベントの条件を経路に分割する"],
            )
        # 回数は成立。引数の一致を Z3 で検査
        conj: list[Any] = []
        for e in sel.must + sel.may:
            for name, req_expr in req.arguments_equal.items():
                if name not in e.arguments:
                    return _PathResult(
                        "UNKNOWN",
                        "unsupported_analysis",
                        f"イベント（seq {e.sequence}）の引数 {name} を解決できない",
                        expected,
                        actual,
                        next_steps=["bindings.operations の argument_bindings を確認する"],
                    )
                conj.append(self.ctx.encode(e.arguments[name]) == self.ctx.encode(req_expr))
        if not conj:
            return self._holds(path, expected, channel=channel)
        s = _solver(self.ctx, self.config.solver_timeout_ms)
        s.add(*base, *post, z3.Not(z3.And(*conj)))
        r = _check(s)
        if r == "unknown":
            return self._solver_limit(f"経路 {path.path_id} の引数一致")
        if r == "unsat":
            return self._holds(path, expected, channel=channel)
        model = s.model()
        arg_actual = "; ".join(
            f"{name} = {self._describe_expr(e.arguments[name])}"
            for e in sel.must + sel.may
            for name in req.arguments_equal
        )
        return self._violation(
            rule, path, "argument_mismatch", expected, arg_actual, model, channel=channel
        )

    # -- results ------------------------------------------------------------
    def _blocking_unknowns(self, path: Path, channel: str | None) -> list[UnknownCause]:
        """この要求の判定を妨げる未解決要因（S29 構造解析）。

        触れ得る観測（may_affect）が分かっている要因は、要求の観測（channel）に触れなければ
        妨げにならない。式の検査（channel=None）は状態・応答に関わるので、呼出しにしか触れない
        領域は妨げにならない（書込み・終了結果に触れる領域は解析器が経路を打ち切っている）。
        """
        blocking: list[UnknownCause] = []
        for u in path.unknown_causes:
            if u.affects == ["arguments_only"]:
                continue
            if u.may_affect is None or (channel is not None and channel in u.may_affect):
                blocking.append(u)
        return blocking

    def _holds(self, path: Path, expected: str, channel: str | None = None) -> _PathResult:
        blocking = self._blocking_unknowns(path, channel)
        if blocking:
            causes = "; ".join(u.detail for u in blocking)
            return _PathResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} は不完全なため成立を保証できない（{causes}）",
                expected,
                next_steps=["未解決要因を解消して経路を完全にする"],
            )
        if path.precision == "incomplete":
            return _PathResult(
                "VERIFIED_IN_SCOPE",
                "conformant",
                "成立（構造解析: 対応外の領域はこの要求の観測に触れない）",
                expected,
            )
        return _PathResult("VERIFIED_IN_SCOPE", "conformant", "成立", expected)

    def _violation(
        self,
        rule: Rule,
        path: Path,
        category: Category,
        expected: str,
        actual: str,
        model: Any,
        channel: str | None = None,
    ) -> _PathResult:
        cex = self._counterexample(path, model, actual)
        if path.precision == "overapproximate":
            return _PathResult(
                "REVIEW",
                "potential_violation",
                f"近似された経路 {path.path_id} で反例が見つかったが、"
                f"実行可能性は未確認（{category}）",
                expected,
                actual,
                cex,
                next_steps=["経路の近似要因を解消するか、テストで再現する"],
            )
        presence_based = category in ("forbidden_effect",)
        if self._blocking_unknowns(path, channel) and not presence_based:
            return _PathResult(
                "UNKNOWN",
                "unsupported_analysis",
                f"経路 {path.path_id} は不完全なため違反と確定できない（{category}）",
                expected,
                actual,
                cex,
                next_steps=["未解決要因を解消して経路を完全にする"],
            )
        return _PathResult(
            "VIOLATED",
            category,
            f"経路 {path.path_id} で要求 {rule.id} に反する: 期待「{expected}」に対し「{actual}」",
            expected,
            actual,
            cex,
        )

    def _counterexample(self, path: Path, model: Any, actual: str) -> Counterexample:
        assignments: dict[str, bool | int | str] = {}
        if model is not None:
            for name in sorted(self.ctx.variables):
                if name.startswith("before."):
                    v = self.ctx.value_of(model, name)
                    if v is not None:
                        assignments[name] = v
        else:
            # 構造的な違反（回数）: 経路条件そのものが反例の条件
            assignments = {}
        cond = self._describe_expr(path.guard)
        return Counterexample(
            kind="symbolic",
            path_id=path.path_id,
            assignments=assignments,
            description=f"条件: {cond}; 実装: {actual}",
        )

    # -- pretty -------------------------------------------------------------
    def _describe_expr(self, expr: Expr) -> str:
        match expr:
            case Ref():
                return expr.path
            case Const():
                return str(expr.value)
            case _ if hasattr(expr, "arg"):
                return f"not({self._describe_expr(expr.arg)})"
            case Binary():
                sym = {
                    "eq": "==",
                    "ne": "!=",
                    "lt": "<",
                    "le": "<=",
                    "gt": ">",
                    "ge": ">=",
                    "add": "+",
                    "sub": "-",
                }[expr.op]
                return f"{self._describe_expr(expr.left)} {sym} {self._describe_expr(expr.right)}"
            case _:
                joiner = " and " if expr.op == "and" else " or "
                return "(" + joiner.join(self._describe_expr(a) for a in expr.args) + ")"


def evaluate(
    spec: SpecIR,
    code: CodeIR,
    domain: DomainSpec,
    config: EngineConfig | None = None,
    run_id: str | None = None,
) -> Findings:
    """便利関数。1 回の検査に 1 つの ConformanceEngine を使う。"""
    if config is None:
        config = EngineConfig(solver_timeout_ms=code.limits.solver_timeout_ms)
    return ConformanceEngine(domain, config).evaluate(spec, code, run_id)


__all__ = [
    "ENGINE_VERSION",
    "BOOL_TYPE",
    "ConformanceEngine",
    "EngineConfig",
    "EngineInputError",
    "evaluate",
]
