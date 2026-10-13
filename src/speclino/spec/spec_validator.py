"""spec_validator。形式検証（pydantic）の後段で、参照解決・型整合・
語彙対応・矛盾・承認ハッシュを Diagnostic として列挙する。

engine は同じ検査を EngineInputError として投げるが、validator は検査前に全件をまとめて返す。
型検査は conformance.typing を再利用し、二重実装しない。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable

from speclino.conformance.typing import IRTypeError, TypeEnv, infer_type, require_boolean
from speclino.domain.bindings import Bindings
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Binary, Const, Expr, Ref, refs_in
from speclino.domain.spec_ir import EventRequirement, ExpressionAssertion, Rule, SpecIR
from speclino.spec.diagnostics import Diagnostic, ValidationReport
from speclino.spec.loader import content_hash

PENDING_HASH = "sha256:pending"
Emit = Callable[[str, "Rule | None", "str | None", str], None]


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rule_content_hash(rule: Rule) -> str:
    """Rule の正規化 JSON（approval_ref を除き既定値を省く）のハッシュ。承認対象は「解釈」。

    既定値を省くことで、後から任意フィールドが増えても既存の承認ハッシュが変わらない。
    """
    data = rule.model_dump(mode="json", exclude_defaults=True)
    data.pop("approval_ref", None)
    return content_hash(_canonical(data))


def rules_content_hash(rules: list[Rule]) -> str:
    """順序に依存しない Rule 集合のハッシュ（rule.id でソート）。"""
    items = sorted((r.id, rule_content_hash(r)) for r in rules)
    return content_hash(_canonical(items))


def validate_spec_ir(
    spec: SpecIR,
    domain: DomainSpec,
    bindings: Bindings | None = None,
    spec_markdown: str | None = None,
) -> ValidationReport:
    env = TypeEnv(domain)
    diags: list[Diagnostic] = []
    approvals = {a.id: a for a in spec.approvals}

    def err(code: str, rule: Rule | None, path: str | None, msg: str) -> None:
        diags.append(
            Diagnostic(
                code=code,
                severity="error",
                rule_id=rule.id if rule else None,
                path=path,
                message=msg,
            )
        )

    def warn(code: str, rule: Rule | None, path: str | None, msg: str) -> None:
        diags.append(
            Diagnostic(
                code=code,
                severity="warning",
                rule_id=rule.id if rule else None,
                path=path,
                message=msg,
            )
        )

    # 重複 ID
    seen: dict[str, int] = defaultdict(int)
    for rule in spec.rules:
        seen[rule.id] += 1
    for rid, n in seen.items():
        if n > 1:
            err("E_DUP_RULE_ID", None, None, f"rule.id が重複: {rid} ({n} 件)")

    for rule in spec.rules:
        _check_expr(env, rule, rule.applicability, "applicability", err, boolean=True)
        for rf in refs_in(rule.applicability):
            if rf.namespace != "before":
                err(
                    "E_APPLICABILITY_NAMESPACE",
                    rule,
                    "applicability",
                    f"before 以外を参照: {rf.path}",
                )
        a = rule.assertion
        if isinstance(a, ExpressionAssertion):
            _check_expr(env, rule, a.expr, "assertion.expr", err, boolean=True)
        else:
            _check_event_requirement(env, rule, a, domain, bindings, err)
        # 承認
        if rule.approval_ref is None or rule.approval_ref not in approvals:
            warn(
                "W_UNAPPROVED",
                rule,
                "approval_ref",
                "承認記録が無い（engine では REVIEW: stale_approval）",
            )
            continue
        ap = approvals[rule.approval_ref]
        if spec_markdown is not None:
            if ap.spec_content_hash == PENDING_HASH:
                warn(
                    "W_HASH_PENDING", rule, "approval.spec_content_hash", "仕様本文ハッシュが未確定"
                )
            elif ap.spec_content_hash != content_hash(spec_markdown):
                err(
                    "E_SPEC_HASH_MISMATCH",
                    rule,
                    "approval.spec_content_hash",
                    "承認時の仕様本文と現在の本文が一致しない（再承認が必要）",
                )
    # IR ハッシュ（承認ごとに、その承認を参照する Rule 群で照合）
    for ap_id, ap in approvals.items():
        group = [r for r in spec.rules if r.approval_ref == ap_id]
        if not group:
            continue
        if ap.ir_content_hash == PENDING_HASH:
            warn(
                "W_HASH_PENDING", None, f"approvals[{ap_id}].ir_content_hash", "IR ハッシュが未確定"
            )
        elif ap.ir_content_hash != rules_content_hash(group):
            warn(
                "W_IR_HASH_MISMATCH",
                None,
                f"approvals[{ap_id}].ir_content_hash",
                f"承認時の IR と現在の IR が一致しない（現在: {rules_content_hash(group)}）",
            )
    _check_contradictions(spec.rules, err)
    return ValidationReport(spec=spec, diagnostics=diags)


def _check_expr(env: TypeEnv, rule: Rule, expr: Expr, path: str, err: Emit, boolean: bool) -> None:
    emit = err
    for r in refs_in(expr):
        try:
            env.type_of_ref(r)
        except IRTypeError as e:
            emit("E_REF_UNDEFINED", rule, path, str(e))
            return
    try:
        if boolean:
            require_boolean(env, expr, path)
        else:
            infer_type(env, expr)
    except IRTypeError as e:
        emit("E_TYPE", rule, path, str(e))


def _check_event_requirement(
    env: TypeEnv,
    rule: Rule,
    a: EventRequirement,
    domain: DomainSpec,
    bindings: Bindings | None,
    err: Emit,
) -> None:
    emit = err
    for name, e in a.arguments_equal.items():
        path = f"assertion.arguments_equal[{name}]"
        _check_expr(env, rule, e, path, err, boolean=False)
        for r in refs_in(e):
            if r.namespace != "before":
                emit("E_ARGS_NAMESPACE", rule, path, f"before 以外を参照: {r.path}")
    if a.event_kind == "write":
        if a.operation_or_target not in domain.variables:
            emit(
                "E_OPERATION_UNBOUND",
                rule,
                "assertion.operation_or_target",
                f"書込み対象 {a.operation_or_target} は domain.variables に無い",
            )
        return
    if bindings is None:
        return
    op = bindings.operations.get(a.operation_or_target)
    if op is None:
        emit(
            "E_OPERATION_UNBOUND",
            rule,
            "assertion.operation_or_target",
            f"操作 {a.operation_or_target} は bindings.operations に無い",
        )
        return
    for name in a.arguments_equal:
        if name not in op.argument_bindings:
            emit(
                "E_ARG_UNBOUND",
                rule,
                f"assertion.arguments_equal[{name}]",
                f"引数 {name} は {a.operation_or_target} の argument_bindings に無い",
            )


def _check_contradictions(rules: list[Rule], err: Emit) -> None:
    """同じ適用条件で、同じ Ref を異なる Const と eq で要求する expression 同士（構造比較）。"""
    by_app: dict[str, list[tuple[Rule, Ref, Const]]] = defaultdict(list)
    for rule in rules:
        a = rule.assertion
        if not isinstance(a, ExpressionAssertion) or rule.modality != "must":
            continue
        e = a.expr
        if isinstance(e, Binary) and e.op == "eq":
            pair = _ref_const(e)
            if pair is not None:
                by_app[_canonical(rule.applicability.model_dump(mode="json"))].append((rule, *pair))
    for items in by_app.values():
        for i, (r1, ref1, c1) in enumerate(items):
            for r2, ref2, c2 in items[i + 1 :]:
                if ref1.path == ref2.path and (c1.type, c1.value) != (c2.type, c2.value):
                    err(
                        "E_CONTRADICTION",
                        r1,
                        "assertion.expr",
                        f"{r1.id} と {r2.id} が同じ適用条件で {ref1.path} に異なる値を要求"
                        f"（{c1.value} / {c2.value}）",
                    )


def _ref_const(e: Binary) -> tuple[Ref, Const] | None:
    if isinstance(e.left, Ref) and isinstance(e.right, Const):
        return e.left, e.right
    if isinstance(e.right, Ref) and isinstance(e.left, Const):
        return e.right, e.left
    return None


__all__ = ["rule_content_hash", "rules_content_hash", "validate_spec_ir"]
