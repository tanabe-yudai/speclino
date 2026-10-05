"""新しい規約検査器（S69）が共有する Finding の組み立てとパラメーター検証。

既存の検査器と同じ形（`id=policy:<requirement_id>`、`requirement_id=<rule.id>:<path>`、
`claim_scope=persisted_state`）にそろえる。
"""

from __future__ import annotations

from speclino.domain.common import IRModel, SourceRef
from speclino.domain.finding import Category, Finding, Severity, Verdict
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot


def parse_params[P: IRModel](model: type[P], rule: PolicyRule) -> P:
    """`rule.verifier.params` をモデルで検証する。不正なら `ValueError`。"""
    try:
        return model.model_validate(rule.verifier_ref.params)
    except ValueError as e:  # pydantic.ValidationError は ValueError の派生
        raise ValueError(
            f"{rule.id}: 検証器 {rule.verifier_ref.id} のパラメーターが不正です: {e}"
        ) from e


def severity_of(rule: PolicyRule) -> Severity:
    return "high" if rule.level == "mandatory" else "medium"


def make_finding(
    rule: PolicyRule,
    snapshot: Snapshot,
    path: str,
    verdict: Verdict,
    category: Category,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    lines: list[int] | None = None,
) -> Finding:
    requirement_id = f"{rule.id}:{path}"
    code_refs = (
        [SourceRef(snapshot_id=snapshot.snapshot_id, path=path, line_start=n) for n in lines]
        if lines
        else []
    )
    return Finding(
        id=f"policy:{requirement_id}",
        requirement_id=requirement_id,
        behavior_id=f"policy:{rule.verifier_ref.id}",
        verdict=verdict,
        category=category,
        severity=severity_of(rule),
        message=message,
        expected=expected,
        actual=actual,
        claim_scope="persisted_state",
        code_refs=code_refs,
    )


def unknown_finding(
    rule: PolicyRule, snapshot: Snapshot, path: str, message: str, actual: str
) -> Finding:
    return make_finding(
        rule, snapshot, path, "UNKNOWN", "unsupported_analysis", message, actual=actual
    )
