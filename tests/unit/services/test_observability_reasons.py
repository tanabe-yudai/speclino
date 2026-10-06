"""S71: UNKNOWN の理由の分類・規約の種類・割合（純粋関数）。"""

from __future__ import annotations

from pathlib import Path

import pytest

import speclino
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


_SRC = Path(speclino.__file__).parent

# (検査器のソース, ソースにある言い回し, その言い回しで出るメッセージの例, 理由)。
# 検査器の文言が変わればソースとの突き合わせで落ち、分類の更新漏れに気付ける。
PINNED: list[tuple[str, str, str, UnknownReason]] = [
    (
        "conformance/trace_check.py",
        'f"図の要素に対応する操作が見つからない: {',
        "図の要素に対応する操作が見つからない: pay, ship",
        "unbound",
    ),
    (
        "conformance/trace_check.py",
        '"入口の操作に解析経路が無い"',
        "入口の操作に解析経路が無い",
        "unbound",
    ),
    (
        "conformance/state_check.py",
        'f"状態の属性 {sm.attribute} に対応する列挙型の変数が見つからない"',
        "状態の属性 status に対応する列挙型の変数が見つからない",
        "unbound",
    ),
    (
        "conformance/state_check.py",
        "が列挙 {ty} に無い（対応が見つからない）",
        "図の状態 Paid, Shipped が列挙 Status に無い（対応が見つからない）",
        "unbound",
    ),
    ("conformance/state_check.py", '"解析経路が無い"', "解析経路が無い", "unbound"),
    (
        "services/check_pipeline.py",
        'f"図の遷移を行うコードが対応に無い（',
        "図の遷移を行うコードが対応に無い（bindings のどの振る舞いの解析経路にも代入が無い）",
        "unbound",
    ),
    (
        "conformance/obligation.py",
        "は解析の精度が {path.precision} のため判定しない",
        "経路 p1 は解析の精度が partial のため判定しない（代入先 x は bindings に無い）",
        "inexact_path",
    ),
    (
        "conformance/obligation.py",
        "の未解決要因が検査する観測に触れ得る",
        "経路 p1 の未解決要因が検査する観測に触れ得る（dynamic call）",
        "inexact_path",
    ),
    (
        "conformance/trace_check.py",
        "が条件付き（may）のため順序を確定できない",
        "経路 p1 の呼出し pay が条件付き（may）のため順序を確定できない",
        "inexact_path",
    ),
    (
        "conformance/state_check.py",
        "への代入が条件付き（may）",
        "経路 p1 の status への代入が条件付き（may）",
        "inexact_path",
    ),
    (
        "conformance/engine.py",
        "は不完全なため成立を保証できない",
        "経路 p1 は不完全なため成立を保証できない（while）",
        "inexact_path",
    ),
    (
        "conformance/engine.py",
        "のイベント抽出が不完全なため",
        "経路 p1 のイベント抽出が不完全なため「呼出しなし」を根拠にできない",
        "inexact_path",
    ),
    (
        "conformance/trace_check.py",
        'f"振る舞い全体の未解決要因が呼出しの観測に触れ得る',
        "振る舞い全体の未解決要因が呼出しの観測に触れ得る（x）",
        "inexact_path",
    ),
    (
        "conformance/state_check.py",
        '"入力の一部がどの解析経路にも対応しない（経路の列挙が不完全）"',
        "入力の一部がどの解析経路にも対応しない（経路の列挙が不完全）",
        "inexact_path",
    ),
    (
        "conformance/trace_check.py",
        '"網羅性の判定がソルバー上限に達した"',
        "網羅性の判定がソルバー上限に達した",
        "analysis_limit",
    ),
    (
        "conformance/trace_check.py",
        'f"図の模擬が上限に達した（{e}）"',
        "図の模擬が上限に達した（64）",
        "analysis_limit",
    ),
    (
        "conformance/engine.py",
        "の判定がソルバー上限（{self.config.solver_timeout_ms}ms）に達した",
        "網羅性の判定がソルバー上限（5000ms）に達した",
        "analysis_limit",
    ),
]


@pytest.mark.parametrize(("source", "literal", "message", "expected"), PINNED)
def test_reason_wording_is_pinned_to_checkers(
    source: str, literal: str, message: str, expected: UnknownReason
) -> None:
    assert literal in (_SRC / source).read_text(encoding="utf-8"), f"{source} の文言が変わった"
    assert unknown_reason(_f(message)) == expected


def test_missing_finding_and_lock() -> None:
    assert unknown_reason(_f("要求 R-1 の判定結果が出ていない", "missing_finding")) == (
        "missing_finding"
    )
    assert unknown_reason(_f("lock.json が無い", rid=LOCK_REQUIREMENT_ID)) == "lock"
    assert unknown_reason(_f("x", "solver_limit")) == "analysis_limit"


@pytest.mark.parametrize(
    "message",
    [
        # 広い言い回しに引っかからない（接頭辞が違えば other）
        "何かが分からない",
        "ほかの説明（対応が見つからない）",
        "設定は対応に無いかもしれない",
        "代入先 x は bindings に無い",
    ],
)
def test_unrecognized_is_other(message: str) -> None:
    assert unknown_reason(_f(message)) == "other"


def _rule(verifier: str | None, level: str = "advisory", tier: str = "deterministic") -> PolicyRule:
    return PolicyRule.model_validate(
        {
            "tier": tier,
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
    assert rule_tier(_rule("llm.checklist", tier="llm_assisted")) == "llm_assisted"
    assert rule_tier(_rule("make.targets", "mandatory")) == "deterministic"


def test_ratio_none_when_nothing_measured() -> None:
    assert ratio(0, 0) is None
    r = ratio(2, 3)
    assert r is not None and (r.numerator, r.denominator) == (2, 3)
    assert r.rate == pytest.approx(2 / 3)
