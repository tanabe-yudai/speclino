"""LLM 支援の規約の評価（S70、設計 6.2–6.3）。LLM は偽物（ネットワークなし）。"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from speclino.domain.assessment import AssessmentUnit
from speclino.domain.policy import PolicyRule
from speclino.ports.text_llm import JsonGeneration
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.llm_policies import (
    CONTENT_HASH_REF,
    FINGERPRINT_REF,
    CallBudget,
    InMemoryAssessmentStore,
    assess,
    fingerprint,
    key_str,
    parse_key,
)
from speclino.services.llm_units import normalized_hash


def _rule(level: str = "mandatory", items: list[str] | None = None) -> PolicyRule:
    return PolicyRule.model_validate(
        {
            "id": "ERRMSG-NEXT",
            "version": "1",
            "level": level,
            "title": "エラーは次の行動を示す",
            "text": "利用者が次に何をすべきかを書く",
            "applies_to": {"paths": ["app/*.py"]},
            "on_missing_input": "unknown",
            "tier": "llm_assisted",
            "verifier": {
                "id": "llm.checklist",
                "version": "1",
                "params": {
                    "items": [{"id": i, "question": f"{i} の質問"} for i in items or ["next"]],
                    "unit_kind": "string_literal",
                },
            },
        }
    )


def _unit(
    text: str = "注文が無い", symbol: str = 'app/api.py::cancel::"注文が無い"'
) -> AssessmentUnit:
    return AssessmentUnit(
        symbol=symbol, kind="string_literal", text=text, unit_hash=normalized_hash(text)
    )


def _answer(applies: bool, quote: str = "注文が無い", item: str = "next") -> str:
    return json.dumps({"items": [{"item_id": item, "applies": applies, "quote": quote}]})


class FakeLlm:
    """呼出しごとに answers を順に返す（尽きたら最後）。例外なら raise する。"""

    model = "fake-model"
    max_output_tokens = 100

    def __init__(self, answers: list[str | Exception], delay: float = 0.0) -> None:
        self.answers = answers
        self.delay = delay
        self.calls = 0

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return JsonGeneration(answer, 10, 5)


async def test_same_unit_same_key_no_llm_call() -> None:
    store = InMemoryAssessmentStore()
    llm = FakeLlm([_answer(True)])
    first, keys1 = await assess(_rule(), [_unit()], llm=llm, store=store, policy_revision=3)
    assert llm.calls == 3
    # 同じ内容（別の場所・同じ hash）でも同じキー → 呼ばない
    second, keys2 = await assess(_rule(), [_unit()], llm=llm, store=store, policy_revision=3)
    assert llm.calls == 3
    assert keys1 == keys2 and len(keys1) == 1
    assert [f.model_dump() for f in first] == [f.model_dump() for f in second]
    # 規約の改訂が変わればキーも変わる
    await assess(_rule(), [_unit()], llm=llm, store=store, policy_revision=4)
    assert llm.calls == 6


async def test_majority_of_three() -> None:
    llm = FakeLlm([_answer(True), _answer(False, ""), _answer(True, "注文")])
    findings, _ = await assess(
        _rule(), [_unit()], llm=llm, store=InMemoryAssessmentStore(), policy_revision=1
    )
    (f,) = findings
    assert f.verdict == "REVIEW"
    assert f.assessment_outcome == "issue"
    assert f.actual == "注文が無い"  # 根拠の引用
    assert f.expected == "next の質問"  # チェック項目
    assert f.code_refs[0].symbol == _unit().symbol  # 場所
    assert f.requirement_id == "ERRMSG-NEXT:app/api.py"
    fp = fingerprint("ERRMSG-NEXT", _unit().symbol, _unit().unit_hash, "next")
    assert f"{FINGERPRINT_REF}{fp}" in f.evidence_refs
    assert f"{CONTENT_HASH_REF}{_unit().unit_hash}" in f.evidence_refs
    assert " " not in fp

    none = FakeLlm([_answer(False, "")])
    findings, _ = await assess(
        _rule(), [_unit()], llm=none, store=InMemoryAssessmentStore(), policy_revision=1
    )
    assert findings[0].verdict == "REVIEW" and findings[0].assessment_outcome == "no_issue"
    assert "機械検証ではない" in findings[0].message


async def test_split_is_review_split() -> None:
    llm = FakeLlm([_answer(True), _answer(False, ""), _answer(False, "")])
    findings, _ = await assess(
        _rule(), [_unit()], llm=llm, store=InMemoryAssessmentStore(), policy_revision=1
    )
    assert [(f.verdict, f.assessment_outcome) for f in findings] == [("REVIEW", "split")]


async def test_items_are_judged_separately() -> None:
    answer = json.dumps(
        {
            "items": [
                {"item_id": "a", "applies": True, "quote": "x"},
                {"item_id": "b", "applies": False, "quote": ""},
            ]
        }
    )
    findings, _ = await assess(
        _rule(items=["a", "b"]),
        [_unit()],
        llm=FakeLlm([answer]),
        store=InMemoryAssessmentStore(),
        policy_revision=1,
    )
    assert [f.assessment_outcome for f in findings] == ["issue", "no_issue"]
    assert len({f.id for f in findings}) == 2


@pytest.mark.parametrize(
    ("answers", "delay"),
    [
        ([RuntimeError("503")], 0.0),
        (["これは JSON ではない"], 0.0),
        ([json.dumps({"items": [{"item_id": "other", "applies": True, "quote": ""}]})], 0.0),
        ([_answer(True), RuntimeError("429")], 0.0),
        ([_answer(True)], 1.0),
    ],
)
async def test_llm_failure_is_unknown(answers: list[str | Exception], delay: float) -> None:
    store = InMemoryAssessmentStore()
    findings, keys = await assess(
        _rule(),
        [_unit()],
        llm=FakeLlm(answers, delay),
        store=store,
        policy_revision=1,
        timeout_s=0.05,
    )
    assert [f.verdict for f in findings] == ["UNKNOWN"]
    assert findings[0].assessment_outcome is None
    assert findings[0].requirement_id == "ERRMSG-NEXT:app/api.py"
    assert keys == [] and store.records == {}  # 失敗は記録しない


async def test_budget_exhausted_is_unknown_but_cache_still_used() -> None:
    store = InMemoryAssessmentStore()
    await assess(_rule(), [_unit()], llm=FakeLlm([_answer(True)]), store=store, policy_revision=1)
    guard = CostGuard(
        InMemoryLedger([]), Decimal("0"), pricing={"fake-model": (Decimal(1), Decimal(1))}
    )
    llm = FakeLlm([_answer(True)])
    other = _unit("在庫が無い", 'app/api.py::f::"在庫が無い"')
    findings, keys = await assess(
        _rule(), [_unit(), other], llm=llm, store=store, policy_revision=1, cost_guard=guard
    )
    assert llm.calls == 0
    by_symbol = {f.code_refs[0].symbol: f for f in findings}
    assert by_symbol[_unit().symbol].assessment_outcome == "issue"  # 記録
    assert by_symbol[other.symbol].verdict == "UNKNOWN"
    assert "費用ガード" in by_symbol[other.symbol].message
    assert len(keys) == 1


async def test_cost_is_recorded() -> None:
    ledger = InMemoryLedger([])
    guard = CostGuard(ledger, Decimal("10"), pricing={"fake-model": (Decimal(1), Decimal(1))})
    await assess(
        _rule(),
        [_unit()],
        llm=FakeLlm([_answer(True)]),
        store=InMemoryAssessmentStore(),
        policy_revision=1,
        cost_guard=guard,
    )
    assert len(ledger.rows) == 3


async def test_replay_only_uses_records() -> None:
    store = InMemoryAssessmentStore()
    _, keys = await assess(
        _rule(), [_unit()], llm=FakeLlm([_answer(True)]), store=store, policy_revision=1
    )
    findings, replay_keys = await assess(
        _rule(), [_unit()], llm=None, store=store, policy_revision=1, model="fake-model"
    )
    assert replay_keys == keys and findings[0].assessment_outcome == "issue"
    missing, _ = await assess(
        _rule(), [_unit("新しい")], llm=None, store=store, policy_revision=1, model="fake-model"
    )
    assert [f.verdict for f in missing] == ["UNKNOWN"]


async def test_over_limit_and_unreadable_are_unknown() -> None:
    rule = _rule()
    rule.verifier_ref.params["max_units"] = 1
    units = [_unit("a", 'app/a.py::f::"a"'), _unit("b", 'app/b.py::f::"b"')]
    findings, _ = await assess(
        rule,
        units,
        llm=FakeLlm([_answer(False, "")]),
        store=InMemoryAssessmentStore(),
        policy_revision=1,
        unassessable=[("app/broken.py", "ファイルを読めない（構文エラーなど）")],
    )
    verdicts = sorted((f.requirement_id, f.verdict) for f in findings)
    assert verdicts == [
        ("ERRMSG-NEXT", "UNKNOWN"),
        ("ERRMSG-NEXT:app/a.py", "REVIEW"),
        ("ERRMSG-NEXT:app/broken.py", "UNKNOWN"),
    ]


async def test_broken_record_is_unknown() -> None:
    """記録の票の数・区分が合わなければ使わない（書き換え・破損で no_issue に化けさせない）。"""
    store = InMemoryAssessmentStore()
    await assess(_rule(), [_unit()], llm=FakeLlm([_answer(True)]), store=store, policy_revision=1)
    (key,) = store.records
    record = store.records[key]
    item = record.items[0]
    for broken in (
        item.model_copy(update={"outcome": "no_issue"}),
        item.model_copy(update={"votes": [False]}),
    ):
        store.records[key] = record.model_copy(update={"items": [broken]})
        llm = FakeLlm([_answer(False, "")])
        findings, keys = await assess(_rule(), [_unit()], llm=llm, store=store, policy_revision=1)
        assert [f.verdict for f in findings] == ["UNKNOWN"] and keys == []
        assert "壊れている" in findings[0].message and llm.calls == 0


async def test_call_budget_per_run_is_shared_and_capped() -> None:
    budget = CallBudget(max_calls=4)
    llm = FakeLlm([_answer(True)])
    units = [_unit("a", 'app/a.py::f::"a"'), _unit("b", 'app/b.py::f::"b"')]
    findings, keys = await assess(
        _rule(),
        units,
        llm=llm,
        store=InMemoryAssessmentStore(),
        policy_revision=1,
        call_budget=budget,
    )
    assert llm.calls == 3 and budget.used == 3 and len(keys) == 1
    assert sorted(f.verdict for f in findings) == ["REVIEW", "UNKNOWN"]
    assert any("上限" in f.message for f in findings if f.verdict == "UNKNOWN")


async def test_keys_are_json_and_include_the_rule_origin() -> None:
    store = InMemoryAssessmentStore()
    llm = FakeLlm([_answer(True)])
    _, team = await assess(
        _rule(), [_unit()], llm=llm, store=store, policy_revision=1, origin="team:w#1"
    )
    _, project = await assess(
        _rule(), [_unit()], llm=llm, store=store, policy_revision=1, origin="project:abc"
    )
    assert llm.calls == 6  # 出所が違えば同じ ID・同じ改訂番号でも記録を共有しない
    key = parse_key(team[0])
    assert key is not None and key.rule_id == "ERRMSG-NEXT@team:w#1"
    assert parse_key(project[0]) is not None and team != project
    assert key_str(key) == team[0]


# ---------------------------------------------------------------------------
# 秘密情報・個人情報を LLM に送らない（#114）
# ---------------------------------------------------------------------------


async def test_unit_with_secret_is_unknown_and_never_sent() -> None:
    token = "gh" + "p_" + "A1b2C3d4E5" * 4
    llm = FakeLlm([_answer(True)])
    store = InMemoryAssessmentStore()
    findings, keys = await assess(
        _rule(), [_unit(f"token {token}")], llm=llm, store=store, policy_revision=1
    )
    (f,) = findings
    assert f.verdict == "UNKNOWN" and "秘密情報らしい値を含むため LLM に送らない" in f.message
    assert token not in f.message  # 値そのものは出さない
    assert llm.calls == 0 and keys == []


async def test_quote_from_model_is_masked_before_storing_and_display() -> None:
    email = "taro.yamada@acme-corp.co.jp"
    llm = FakeLlm([_answer(True, f"連絡先 {email}")])
    store = InMemoryAssessmentStore()
    findings, keys = await assess(_rule(), [_unit()], llm=llm, store=store, policy_revision=1)
    (f,) = findings
    assert f.actual is not None and email not in f.actual and "伏せた" in f.actual
    record = await store.get(parse_key(keys[0]))
    assert record is not None and all(email not in (i.quote or "") for i in record.items)
