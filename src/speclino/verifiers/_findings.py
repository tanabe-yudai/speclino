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
    """`rule.verifier.params` をモデルで検証する。不正なら `ValueError`。

    呼び出し側は `ValueError` を `invalid_params_finding` で UNKNOWN の Finding にし、run 全体を
    落とさない（未定義キーを持つ既存の改訂があっても、他の規約の検査は続く）。
    """
    return model.model_validate(rule.verifier_ref.params)


def invalid_params_finding(rule: PolicyRule, error: ValueError) -> Finding:
    return Finding(
        id=f"policy:{rule.id}:invalid_params",
        requirement_id=rule.id,
        behavior_id=f"policy:{rule.verifier_ref.id}",
        verdict="UNKNOWN",
        category="unsupported_analysis",
        severity=severity_of(rule),
        message=f"{rule.id}: 規約のパラメーターが不正なため検査できません",
        actual=str(error),
        claim_scope="persisted_state",
    )


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


def missing_input_outcome(rule: PolicyRule) -> tuple[Verdict, Category] | None:
    """`on_missing_input` から、対象が無いときの (verdict, category) を決める。skipped は None。"""
    if rule.on_missing_input == "skipped":
        return None
    if rule.on_missing_input == "violation":
        return "VIOLATED", "missing_effect"
    return "UNKNOWN", "unsupported_analysis"


def missing_input_finding(
    rule: PolicyRule, snapshot: Snapshot, path: str, what: str
) -> Finding | None:
    """検査対象（ファイル）が無いときの Finding。`rule.on_missing_input` に従う。"""
    outcome = missing_input_outcome(rule)
    if outcome is None:
        return None
    verdict, category = outcome
    return make_finding(
        rule,
        snapshot,
        path,
        verdict,
        category,
        f"{path}: {what}が無いため確認できません",
        expected=f"{what}が存在すること",
        actual="対象なし",
    )
