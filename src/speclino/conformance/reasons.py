"""Finding の分類（category / severity）とメッセージ。判定ロジックは engine.py。"""

from __future__ import annotations

import hashlib

from speclino.domain.finding import Category, Severity, Verdict
from speclino.domain.spec_ir import Rule

VERDICT_PRIORITY: dict[Verdict, int] = {
    "VIOLATED": 0,
    "REVIEW": 1,
    "UNKNOWN": 2,
    "VERIFIED_IN_SCOPE": 3,
}


# 同じ verdict の経路が複数あるときに代表として報告する category の優先順位（本質的なものを先に）
CATEGORY_PRIORITY: dict[str, int] = {
    "constraint_mismatch": 0,
    "forbidden_effect": 1,
    "missing_effect": 2,
    "state_mismatch": 3,
    "argument_mismatch": 4,
    "cardinality_mismatch": 5,
    "outcome_mismatch": 6,
    "potential_violation": 7,
    "solver_limit": 8,
    "unsupported_analysis": 9,
}


def category_rank(category: str) -> int:
    return CATEGORY_PRIORITY.get(category, 50)


def worst(verdicts: list[Verdict]) -> Verdict:
    return min(verdicts, key=lambda v: VERDICT_PRIORITY[v]) if verdicts else "UNKNOWN"


def severity_for(rule: Rule, verdict: Verdict) -> Severity:
    if verdict == "VERIFIED_IN_SCOPE":
        return "info"
    if verdict == "VIOLATED":
        return "high" if rule.level == "mandatory" else "medium"
    return "medium" if rule.level == "mandatory" else "low"


def finding_id(rule_id: str, behavior_id: str, path_id: str | None, category: Category) -> str:
    """同一入力で同一になる決定的 ID。"""
    key = f"{behavior_id}|{rule_id}|{path_id or '-'}|{category}"
    return "F-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def is_success_status(status: int) -> bool:
    return 200 <= status < 400


def outcome_category(expected_status: int | None, actual_status: int | None) -> Category:
    """応答コードの不一致。成功/拒否の区分が違えば constraint_mismatch。"""
    if expected_status is None or actual_status is None:
        return "outcome_mismatch"
    if is_success_status(expected_status) != is_success_status(actual_status):
        return "constraint_mismatch"
    return "outcome_mismatch"


def count_category(required_op: str, required: int, actual: int) -> Category:
    if actual < required and required_op in ("eq", "ge"):
        return "missing_effect" if actual == 0 else "cardinality_mismatch"
    if actual > required and required_op in ("eq", "le"):
        return "forbidden_effect" if required == 0 else "cardinality_mismatch"
    return "cardinality_mismatch"
