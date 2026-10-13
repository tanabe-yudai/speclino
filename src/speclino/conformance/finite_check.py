"""有限ドメインでの検算。Z3 の判定を独立にクロスチェックする。

engine.py（Z3 による記号実行）とは別に、小さな有限ドメインを総当たりして同じ
「入口仮定 ∧ 適用条件 ∧ 経路条件 ∧ 終了状態 ∧ ¬保証」を評価する。engine.py は
変更せず、その Findings と突き合わせるだけ。Expr は Python の eval を使わず、
自前の Interpreter で評価する。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from speclino.conformance.engine import evaluate
from speclino.domain.code_ir import CodeIR, Event, Path
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import (
    BOOL_TYPE,
    INT_TYPE,
    Binary,
    Const,
    Expr,
    Nary,
    Not,
    Ref,
    ref,
    refs_in,
)
from speclino.domain.spec_ir import EventRequirement, ExpressionAssertion, Rule, SpecIR

_BEFORE = "before"
_AFTER = "after"


class FiniteCheckError(Exception):
    """式が評価できない（未知参照・型不整合など）。cross_check ではこの rule を skip する。"""


class FiniteCheckSkipped(FiniteCheckError):
    """割当の組合せ数が bounds.max_assignments を超えたため、総当たりを打ち切った。"""


@dataclass(frozen=True)
class Bounds:
    """有限ドメインの範囲。opaque は同一性しか比較しないので 2 値で十分。"""

    integers: tuple[int, ...] = (-1, 0, 1, 2, 3)
    opaque_symbols: tuple[str, ...] = ("a", "b")
    max_assignments: int = 5000


class Interpreter:
    """Expr を Python の値で評価する自前の評価器（eval は使わない）。

    環境のキーは名前空間つきの変数名（例: "before.order.status"）。値は
    boolean/integer は素の bool/int、列挙は値名の文字列、opaque は割り当てた記号文字列。
    """

    def __init__(self, env: Mapping[str, bool | int | str]) -> None:
        self._env = env

    def eval(self, expr: Expr) -> bool | int | str:
        match expr:
            case Ref():
                if expr.path not in self._env:
                    raise FiniteCheckError(f"環境に無い参照: {expr.path}")
                return self._env[expr.path]
            case Const():
                return expr.value
            case Not():
                return not self.eval_bool(expr.arg)
            case Binary():
                return self._eval_binary(expr)
            case Nary():
                return self._eval_nary(expr)
        raise FiniteCheckError(f"未知の式: {expr!r}")

    def eval_bool(self, expr: Expr) -> bool:
        value = self.eval(expr)
        if not isinstance(value, bool):
            raise FiniteCheckError(f"boolean が必要: {value!r}")
        return value

    def _eval_int(self, expr: Expr) -> int:
        value = self.eval(expr)
        if isinstance(value, bool) or not isinstance(value, int):
            raise FiniteCheckError(f"integer が必要: {value!r}")
        return value

    def _eval_binary(self, expr: Binary) -> bool | int:
        if expr.op == "eq":
            return self.eval(expr.left) == self.eval(expr.right)
        if expr.op == "ne":
            return self.eval(expr.left) != self.eval(expr.right)
        left, right = self._eval_int(expr.left), self._eval_int(expr.right)
        if expr.op == "lt":
            return left < right
        if expr.op == "le":
            return left <= right
        if expr.op == "gt":
            return left > right
        if expr.op == "ge":
            return left >= right
        if expr.op == "add":
            return left + right
        if expr.op == "sub":
            return left - right
        raise FiniteCheckError(f"未知の演算子: {expr.op}")

    def _eval_nary(self, expr: Nary) -> bool:
        values = [self.eval_bool(a) for a in expr.args]
        return all(values) if expr.op == "and" else any(values)


def _domain_values(domain: DomainSpec, type_name: str, bounds: Bounds) -> list[bool | int | str]:
    if type_name == BOOL_TYPE:
        return [True, False]
    if type_name == INT_TYPE:
        return list(bounds.integers)
    if domain.is_enum(type_name):
        values = domain.enums.get(type_name) or []
        if not values:
            raise FiniteCheckError(f"列挙 {type_name} の値が空")
        return list(values)
    if domain.is_opaque(type_name):
        return list(bounds.opaque_symbols)
    raise FiniteCheckError(f"未定義の型: {type_name!r}")


def enumerate_assignments(
    domain: DomainSpec, refs: Iterable[Ref], bounds: Bounds | None = None
) -> Iterator[dict[str, bool | int | str]]:
    """参照される before 変数の全組合せを列挙する。

    boolean は {True, False}、列挙は宣言値、integer は bounds.integers、opaque は
    bounds.opaque_symbols から独立に選ぶ（同じ opaque 型の変数が 2 つあれば「等しい」
    「異なる」の両方が組合せに現れる）。組合せ数が bounds.max_assignments を超えたら
    FiniteCheckSkipped を送出する。
    """
    bounds = bounds or Bounds()
    variables = sorted({r.variable for r in refs if r.namespace == _BEFORE})
    value_lists: list[list[bool | int | str]] = []
    for var in variables:
        ty = domain.type_of(var)
        if ty is None:
            raise FiniteCheckError(f"変数が domain に未定義: before.{var}")
        value_lists.append(_domain_values(domain, ty, bounds))

    total = 1
    for values in value_lists:
        total *= len(values)
    if total > bounds.max_assignments:
        raise FiniteCheckSkipped(
            f"割当の組合せ数が上限を超えた: {total} > {bounds.max_assignments}"
        )

    keys = [f"before.{var}" for var in variables]
    for combo in itertools.product(*value_lists):
        yield dict(zip(keys, combo, strict=True))


def _refs_for_check(rule: Rule, path: Path, domain: DomainSpec) -> list[Ref]:
    """rule と path（と入口仮定）の評価に必要な参照を集める。after.X があれば before.X も足す

    （更新が無ければ after.X = before.X にフォールバックするため）。
    """
    exprs: list[Expr] = [*domain.assumptions, rule.applicability, path.guard]
    assertion = rule.assertion
    if isinstance(assertion, ExpressionAssertion):
        exprs.append(assertion.expr)
    else:
        exprs.extend(assertion.arguments_equal.values())
    exprs.extend(su.value for su in path.state_updates)
    for e in path.events:
        exprs.extend(e.arguments.values())
        if e.value is not None:
            exprs.append(e.value)

    # Ref.path で重複を除く（dict はキーの挿入順を保つ）
    refs: dict[str, Ref] = {}
    for expr in exprs:
        for r in refs_in(expr):
            refs.setdefault(r.path, r)
            if r.namespace == _AFTER:
                before = ref(f"{_BEFORE}.{r.variable}")
                refs.setdefault(before.path, before)
    return list(refs.values())


def _extend_post_state(env: dict[str, bool | int | str], path: Path, interp: Interpreter) -> None:
    """経路の終了状態を環境に足す: after.x = 最後の更新値（無ければ before.x）、outcome。"""
    # 同じ対象への更新が複数あれば最後のものが残る
    updated: dict[str, Expr] = {su.target: su.value for su in path.state_updates}
    for target, value in updated.items():
        env[f"{_AFTER}.{target}"] = interp.eval(value)
    before_prefix = f"{_BEFORE}."
    for key in list(env):
        if key.startswith(before_prefix):
            env.setdefault(f"{_AFTER}.{key.removeprefix(before_prefix)}", env[key])
    env["outcome.kind"] = path.outcome.kind
    if path.outcome.status is not None:
        env["outcome.status"] = path.outcome.status


def _count_ok(op: str, required: int, n: int) -> bool:
    return {"eq": n == required, "ge": n >= required, "le": n <= required}[op]


def _matches_event(e: Event, req: EventRequirement) -> bool:
    return (
        e.kind == req.event_kind
        and e.phase == req.phase
        and e.operation_or_target == req.operation_or_target
    )


def _event_requirement_ok(
    rule: Rule, req: EventRequirement, path: Path, interp: Interpreter
) -> bool | Literal["uncertain"]:
    """engine._check_event_requirement と同じ規則で判定する。

    may イベントは「あるかもしれない」ので回数を範囲 [must 数, must 数 + may 数] で見る。
    範囲全体で成立なら成立、全体で不成立なら不成立、混在なら "uncertain"（engine の REVIEW）。
    引数の一致は must / may の全イベントに要求する（engine と同じ）。
    """
    must = [e for e in path.events if _matches_event(e, req) and e.certainty == "must"]
    may = [e for e in path.events if _matches_event(e, req) and e.certainty == "may"]
    op, required = req.count.operator, req.count.value
    oks = [_count_ok(op, required, n) for n in range(len(must), len(must) + len(may) + 1)]
    count_holds, count_fails = all(oks), not any(oks)

    if rule.modality == "must_not":
        if count_fails:
            return True
        if count_holds and not req.arguments_equal:
            return False
        return "uncertain"

    if count_fails:
        return False
    if not count_holds:
        return "uncertain"
    for e in must + may:
        for name, expected in req.arguments_equal.items():
            if name not in e.arguments:
                raise FiniteCheckError(f"イベント（seq {e.sequence}）の引数 {name} を解決できない")
            if interp.eval(e.arguments[name]) != interp.eval(expected):
                return False
    return True


_AssignmentResult = Literal["not_relevant", "holds", "violated", "uncertain"]


def _evaluate_assignment(
    rule: Rule, path: Path, env: dict[str, bool | int | str], domain: DomainSpec
) -> _AssignmentResult:
    interp = Interpreter(env)
    # engine と同じ検査式「入口仮定 ∧ 適用条件 ∧ 経路条件 ∧ …」。仮定に反する割当は対象外
    if not all(interp.eval_bool(a) for a in domain.assumptions):
        return "not_relevant"
    if not interp.eval_bool(path.guard):
        return "not_relevant"
    if not interp.eval_bool(rule.applicability):
        return "not_relevant"
    _extend_post_state(env, path, interp)

    a = rule.assertion
    if isinstance(a, ExpressionAssertion):
        value = interp.eval_bool(a.expr)
        ok: bool | Literal["uncertain"] = value if rule.modality == "must" else not value
    else:
        ok = _event_requirement_ok(rule, a, path, interp)

    if ok == "uncertain":
        return "uncertain"
    return "holds" if ok else "violated"


RuleVerdict = Literal["holds", "violated", "uncertain", "not_applicable"]


def check_rule(
    rule: Rule, path: Path, domain: DomainSpec, bounds: Bounds | None = None
) -> RuleVerdict:
    """rule.applicability と path.guard を before 環境で評価し、両方真なら「関連」。

    関連する割当が一つも無ければ not_applicable。関連する割当のうち一つでも違反
    （assertion が modality に反する）なら violated、違反は無いが may イベントで
    決められない割当があれば uncertain、そうでなければ holds。
    """
    bounds = bounds or Bounds()
    refs = _refs_for_check(rule, path, domain)
    relevant = False
    violated = False
    uncertain = False
    for env in enumerate_assignments(domain, refs, bounds):
        result = _evaluate_assignment(rule, path, dict(env), domain)
        if result == "not_relevant":
            continue
        relevant = True
        if result == "violated":
            violated = True
        elif result == "uncertain":
            uncertain = True
    if not relevant:
        return "not_applicable"
    if violated:
        return "violated"
    return "uncertain" if uncertain else "holds"


def first_counterexample(
    rule: Rule, path: Path, domain: DomainSpec, bounds: Bounds | None = None
) -> dict[str, bool | int | str] | None:
    """反例（関連かつ違反）となる before 割当を一つ返す。無ければ None。"""
    bounds = bounds or Bounds()
    refs = _refs_for_check(rule, path, domain)
    for env in enumerate_assignments(domain, refs, bounds):
        if _evaluate_assignment(rule, path, dict(env), domain) == "violated":
            return dict(env)
    return None


@dataclass(frozen=True)
class CrossCheckReport:
    agreements: int
    disagreements: list[str]
    skipped: list[str]


def cross_check(
    spec: SpecIR, code: CodeIR, domain: DomainSpec, bounds: Bounds | None = None
) -> CrossCheckReport:
    """engine.evaluate の Findings と、有限ドメイン総当たりの判定を rule ごとに突き合わせる。

    比較規則: engine が VIOLATED なら総当たりでも反例が存在するべき、engine が
    VERIFIED_IN_SCOPE なら総当たりで反例が存在しないべき。engine が UNKNOWN / REVIEW
    のときは比較しない（skip）。経路が exact_in_model でない rule、unknown 変数や
    型不整合で評価できない rule も skip する（例外にしない）。
    """
    bounds = bounds or Bounds()
    findings = evaluate(spec, code, domain)
    findings_by_rule = {f.requirement_id: f for f in findings.findings}

    agreements = 0
    disagreements: list[str] = []
    skipped: list[str] = []

    for rule in spec.rules:
        finding = findings_by_rule.get(rule.id)
        if finding is None:
            skipped.append(f"{rule.id}: engine の Finding が無い")
            continue
        if finding.verdict not in ("VERIFIED_IN_SCOPE", "VIOLATED"):
            skipped.append(f"{rule.id}: engine の verdict が {finding.verdict} のため比較しない")
            continue
        if any(p.precision != "exact_in_model" for p in code.paths):
            skipped.append(f"{rule.id}: 経路が exact_in_model でないため比較しない")
            continue

        any_violation = False
        any_uncertain = False
        error: str | None = None
        for path in code.paths:
            try:
                verdict = check_rule(rule, path, domain, bounds)
            except FiniteCheckError as e:
                error = f"{rule.id} / {path.path_id}: {e}"
                break
            if verdict == "violated":
                any_violation = True
            elif verdict == "uncertain":
                any_uncertain = True
        if error is not None:
            skipped.append(error)
            continue

        if finding.verdict == "VIOLATED":
            agrees = any_violation
        else:
            # VERIFIED は「全経路で成立」の主張なので、総当たりで決められない経路があれば不一致
            agrees = not any_violation and not any_uncertain
        if agrees:
            agreements += 1
        else:
            actual = "反例あり" if any_violation else ("不定" if any_uncertain else "反例なし")
            disagreements.append(f"{rule.id}: engine={finding.verdict} / 総当たり={actual}")

    return CrossCheckReport(agreements=agreements, disagreements=disagreements, skipped=skipped)


__all__ = [
    "Bounds",
    "CrossCheckReport",
    "FiniteCheckError",
    "FiniteCheckSkipped",
    "Interpreter",
    "check_rule",
    "cross_check",
    "enumerate_assignments",
    "first_counterexample",
]
