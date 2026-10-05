"""可観測性の集計（S71、設計 9 章）。知識が使われ、守られているかを 1 つの集計にまとめる。

`workspace_health` が画面と REST の唯一の集計で、画面は数え直さない。測っていないものは None
（「未計測」）にし、0% や 100% にしない。クエリの数はプロジェクトの数に比例しない（N+1 にしない）。
"""

from __future__ import annotations

import re

from speclino.domain.finding import Finding
from speclino.domain.observability import Ratio, RuleTier, UnknownReason
from speclino.domain.policy import PolicyRule
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID

LLM_VERIFIER_ID = "llm.checklist"

# 検査器（conformance/・analysis/・services/check_pipeline.py）が UNKNOWN に付ける決まった言い回し。
# Finding に理由の欄は無い（契約を変えない）ので、言い回しで分類する。判別できなければ other。
_UNBOUND = re.compile(
    r"対応する操作が見つからない|対応が見つからない|対応する列挙型の変数が見つからない"
    r"|bindings に無い|対応に無い|^入口の操作に解析経路が無い|^解析経路が無い"
)
_LIMIT = re.compile(r"上限")
_INEXACT = re.compile(
    r"精度が|不完全|条件付き（may）|未解決要因|確定できない|解決できない|判別できない"
    r"|一意に決まらない|どの解析経路にも対応しない|値が分からない"
)


def unknown_reason(f: Finding) -> UnknownReason:
    """UNKNOWN の Finding の理由の区分。"""
    if f.category == "missing_finding":
        return "missing_finding"
    if f.requirement_id == LOCK_REQUIREMENT_ID:
        return "lock"
    if f.category == "solver_limit" or _LIMIT.search(f.message):
        return "analysis_limit"
    if _UNBOUND.search(f.message):
        return "unbound"
    if _INEXACT.search(f.message):
        return "inexact_path"
    return "other"


def rule_tier(rule: PolicyRule) -> RuleTier:
    if rule.verifier is None:
        return "unverified"
    return "llm_assisted" if rule.verifier.id == LLM_VERIFIER_ID else "deterministic"


def ratio(numerator: int, denominator: int) -> Ratio | None:
    """分母が 0 なら None（未計測）。"""
    if denominator == 0:
        return None
    return Ratio(numerator=numerator, denominator=denominator, rate=numerator / denominator)
