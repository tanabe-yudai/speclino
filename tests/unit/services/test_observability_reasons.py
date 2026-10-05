"""S71: UNKNOWN の理由の分類・規約の種類・割合（純粋関数）。"""

from __future__ import annotations

import pytest

from speclino.domain.finding import Category, Finding
from speclino.domain.observability import UnknownReason
from speclino.domain.policy import PolicyRule
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID
from speclino.services.observability import ratio, rule_tier, unknown_reason


def _f(message: str, category: Category = "unsupported_analysis", rid: str = "R-1") -> Finding:
    return Finding(
        id="F-1",
        requirement_id=rid,
        behavior_id="spec:orders",
        verdict="UNKNOWN",
        category=category,
        severity="medium",
        message=message,
        claim_scope="command_invocation",
    )


@pytest.mark.parametrize(
    ("message", "category", "rid", "expected"),
    [
        # 検査器が実際に出すメッセージ（conformance/ と services/check_pipeline.py）
        (
            "要求 R-1 の判定結果が出ていない（検査器の例外…）",
            "missing_finding",
            "R-1",
            "missing_finding",
        ),
        ("lock.json が無い", "unsupported_analysis", LOCK_REQUIREMENT_ID, "lock"),
        ("図の要素に対応する操作が見つからない: pay", "unsupported_analysis", "R-1", "unbound"),
        (
            "図の状態 Paid が列挙 Status に無い（対応が見つからない）",
            "unsupported_analysis",
            "R-1",
            "unbound",
        ),
        (
            "状態の属性 status に対応する列挙型の変数が見つからない",
            "unsupported_analysis",
            "R-1",
            "unbound",
        ),
        ("入口の操作に解析経路が無い", "unsupported_analysis", "R-1", "unbound"),
        ("図の遷移を行うコードが対応に無い（…）", "unsupported_analysis", "R-1", "unbound"),
        ("代入先 x は bindings に無い", "unsupported_analysis", "R-1", "unbound"),
        (
            "経路 p1 は解析の精度が partial のため判定しない（while）",
            "unsupported_analysis",
            "R-1",
            "inexact_path",
        ),
        (
            "経路 p1 の呼出し pay が条件付き（may）のため順序を確定できない",
            "unsupported_analysis",
            "R-1",
            "inexact_path",
        ),
        (
            "経路 p1 は不完全なため成立を保証できない（x）",
            "unsupported_analysis",
            "R-1",
            "inexact_path",
        ),
        (
            "振る舞い全体の未解決要因が呼出しの観測に触れ得る（x）",
            "unsupported_analysis",
            "R-1",
            "inexact_path",
        ),
        ("網羅性の判定がソルバー上限に達した", "unsupported_analysis", "R-1", "analysis_limit"),
        ("図の模擬が上限に達した（64）", "unsupported_analysis", "R-1", "analysis_limit"),
        ("順序の判定がソルバー上限（5000ms）に達した", "solver_limit", "R-1", "analysis_limit"),
        ("何かが分からない", "unsupported_analysis", "R-1", "other"),
    ],
)
def test_unknown_reason(
    message: str, category: Category, rid: str, expected: UnknownReason
) -> None:
    assert unknown_reason(_f(message, category, rid)) == expected


def _rule(verifier: str | None, level: str = "advisory") -> PolicyRule:
    return PolicyRule.model_validate(
        {
            "id": "R",
            "version": "1",
            "level": level,
            "title": "t",
            "text": "t",
            "applies_to": {"paths": []},
            "verifier": None if verifier is None else {"id": verifier, "version": "1"},
            "on_missing_input": "unknown",
        }
    )


def test_rule_tier() -> None:
    assert rule_tier(_rule(None)) == "unverified"
    assert rule_tier(_rule("llm.checklist")) == "llm_assisted"
    assert rule_tier(_rule("make.targets", "mandatory")) == "deterministic"


def test_ratio_none_when_nothing_measured() -> None:
    assert ratio(0, 0) is None
    r = ratio(2, 3)
    assert r is not None and (r.numerator, r.denominator) == (2, 3)
    assert r.rate == pytest.approx(2 / 3)
