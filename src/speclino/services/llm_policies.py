"""LLM 支援の規約の評価（S70、設計 6.2–6.3）。

判定はしない: LLM の答えは常に REVIEW の Finding になり、区分（issue / no_issue / split）は
`assessment_outcome` に入れる。確定は人（`services/attestations.py`）。

揺らぎを防ぐ仕組み:
1. 単位（関数・文字列リテラル・エンドポイント）ごとにキー
   (規約の改訂, ルール ID, 正規化した単位の hash, モデル, プロンプトの版) を作り、記録があれば
   LLM を呼ばずにそれを返す。プロンプトの版にはチェック項目と規約の本文の hash を含める
   （項目を変えたら前の記録は使わない）
2. 新しいキーは 3 回評価する（temperature 0、項目ごとに「該当する／しない＋引用」だけ）。
   「該当する」が 2 回以上 → issue、1 回 → split、0 回 → no_issue。ゲートを止めない no_issue は
   全員一致のときだけにする（安全側）
3. 指紋 = (ルール ID, シンボル, 単位の hash, 項目)。内容が変われば指紋も変わり、確定は外れる

LLM の失敗（例外・時間切れ・形式違反）と費用枠の超過は、その単位の UNKNOWN にする
（VERIFIED にも VIOLATED にもしない）。失敗した評価は記録しない（次の run で評価し直す）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from speclino.domain.assessment import (
    AssessmentKey,
    AssessmentOutcome,
    AssessmentRecord,
    AssessmentUnit,
    ItemVerdict,
)
from speclino.domain.common import SourceRef
from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import LlmChecklistItem, LlmChecklistParams
from speclino.ports.text_llm import TextLlm
from speclino.services.cost_guard import CostGuard

PROMPT_VERSION = "llm-checklist-v1"
TRIALS = 3
DEFAULT_TIMEOUT_S = 60.0
BEHAVIOR_ID = "policy:llm.checklist"
# Finding.evidence_refs の接頭辞。ゲート（確定の照合）と画面が読む
FINGERPRINT_REF = "fingerprint:"
CONTENT_HASH_REF = "content_hash:"
ASSESSMENT_KEY_REF = "assessment_key:"

NO_ISSUE_LABEL = "LLM で確認、指摘なし（機械検証ではない）"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "applies": {"type": "boolean"},
                    "quote": {"type": "string"},
                },
                "required": ["item_id", "applies", "quote"],
            },
        }
    },
    "required": ["items"],
}


class AssessmentStore(Protocol):
    """評価の記録（キー → 項目ごとの結果）。実体は `db/repositories/assessments.py`。"""

    async def get(self, key: AssessmentKey) -> AssessmentRecord | None: ...

    async def put(self, record: AssessmentRecord) -> None: ...


@dataclass
class InMemoryAssessmentStore:
    """テスト・CLI 用。"""

    records: dict[str, AssessmentRecord] = field(default_factory=dict)

    async def get(self, key: AssessmentKey) -> AssessmentRecord | None:
        return self.records.get(key_str(key))

    async def put(self, record: AssessmentRecord) -> None:
        self.records.setdefault(key_str(record.key), record)


class AssessmentFailed(Exception):
    """LLM の評価が使えない（例外・時間切れ・形式違反・費用枠）。その単位は UNKNOWN。"""


def checklist_params(rule: PolicyRule) -> LlmChecklistParams:
    return LlmChecklistParams.model_validate(rule.verifier_ref.params)


def prompt_version(rule: PolicyRule, params: LlmChecklistParams) -> str:
    """プロンプトの版 ＋ チェック項目・単位の種類・規約の本文の hash。"""
    material = json.dumps(
        {
            "items": [i.model_dump() for i in params.items],
            "unit_kind": params.unit_kind,
            "title": rule.title,
            "text": rule.text,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"{PROMPT_VERSION}+{hashlib.sha256(material.encode()).hexdigest()[:16]}"


# 1 run あたりの LLM 呼出しの上限（費用の枠とは別の、暴走の歯止め）
DEFAULT_MAX_CALLS_PER_RUN = 300


@dataclass
class CallBudget:
    """1 run の中で全ルールが共有する呼出しの上限。超える新しい単位は UNKNOWN。"""

    max_calls: int = DEFAULT_MAX_CALLS_PER_RUN
    used: int = 0


def key_str(key: AssessmentKey) -> str:
    """キーの文字列（記録の検索・manifest）。区切り文字の衝突が無い正規 JSON。"""
    return json.dumps(key.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def parse_key(text: str) -> AssessmentKey | None:
    try:
        return AssessmentKey.model_validate(json.loads(text))
    except (ValueError, TypeError):
        return None


def rule_key(rule: PolicyRule, origin: str | None) -> str:
    """キーのルール部分。規約の出所（チーム規約の改訂・プロジェクト規約のファイルの hash）を含め、
    出所の違う同じ ID のルールの記録を取り違えない。"""
    return f"{rule.id}@{origin}" if origin else rule.id


def assessment_key(
    rule: PolicyRule,
    unit: AssessmentUnit,
    *,
    model: str,
    policy_revision: int,
    origin: str | None = None,
) -> AssessmentKey:
    return AssessmentKey(
        policy_revision=policy_revision,
        rule_id=rule_key(rule, origin),
        unit_hash=unit.unit_hash,
        model=model,
        prompt_version=prompt_version(rule, checklist_params(rule)),
    )


def fingerprint(rule_id: str, symbol: str, unit_hash: str, item_id: str) -> str:
    """指紋（設計 6.3 の 2）。PR コメントに書けるよう空白を含まない形にする。"""
    material = json.dumps([rule_id, symbol, unit_hash, item_id], ensure_ascii=False)
    return f"{rule_id}:{item_id}:{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def outcome_of(votes: Sequence[bool]) -> AssessmentOutcome:
    yes = sum(1 for v in votes if v)
    if yes >= 2:
        return "issue"
    if yes == 1:
        return "split"
    return "no_issue"


def build_prompt(rule: PolicyRule, params: LlmChecklistParams, unit: AssessmentUnit) -> str:
    items = "\n".join(f"- {i.id}: {i.question}" for i in params.items)
    return (
        "あなたはチームの規約のレビュー補助です。判定はせず、各チェック項目について、"
        "下のコードがその項目の懸念に該当するか（applies）と、根拠となる箇所の短い引用（quote）"
        "だけを JSON で答えてください。該当しなければ applies=false、quote は空文字。\n\n"
        f"規約: {rule.title}\n{rule.text}\n\n"
        f"チェック項目（item_id: 質問）:\n{items}\n\n"
        f"評価の単位（{unit.kind}）: {unit.symbol}\n"
        "```\n"
        f"{unit.text}\n"
        "```\n"
    )


def parse_response(text: str, params: LlmChecklistParams) -> dict[str, tuple[bool, str]]:
    """項目 ID → (該当する, 引用)。全項目がちょうど 1 回ずつ無ければ AssessmentFailed。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AssessmentFailed(f"JSON として読めない: {e}") from e
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise AssessmentFailed("items が無い")
    wanted = {i.id for i in params.items}
    out: dict[str, tuple[bool, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AssessmentFailed("items の要素がオブジェクトでない")
        item_id, applies, quote = row.get("item_id"), row.get("applies"), row.get("quote", "")
        if item_id not in wanted or item_id in out:
            raise AssessmentFailed(f"未知または重複した項目: {item_id}")
        if not isinstance(applies, bool) or not isinstance(quote, str):
            raise AssessmentFailed(f"{item_id}: applies / quote の型が違う")
        out[item_id] = (applies, quote)
    if out.keys() != wanted:
        raise AssessmentFailed(f"答えの無い項目: {sorted(wanted - out.keys())}")
    return out


@dataclass
class _Budget:
    """費用枠を一度超えたら、残りの新しいキーは呼ばない（記録の再利用は続ける）。"""

    guard: CostGuard | None
    today: date
    run_id: str | None
    calls: CallBudget | None = None
    exhausted: str | None = None


async def _evaluate(
    rule: PolicyRule,
    params: LlmChecklistParams,
    unit: AssessmentUnit,
    key: AssessmentKey,
    *,
    llm: TextLlm,
    budget: _Budget,
    timeout_s: float,
    now: datetime,
) -> AssessmentRecord:
    prompt = build_prompt(rule, params, unit)
    trials: list[dict[str, tuple[bool, str]]] = []
    for _ in range(TRIALS):
        if budget.exhausted is not None:
            raise AssessmentFailed(f"費用ガードにより停止: {budget.exhausted}")
        if budget.calls is not None:
            if budget.calls.used >= budget.calls.max_calls:
                raise AssessmentFailed(
                    f"1 run の LLM 呼出しの上限 {budget.calls.max_calls} 回に達した"
                )
            budget.calls.used += 1
        if budget.guard is not None:
            worst = budget.guard.estimate(llm.model, len(prompt), llm.max_output_tokens)
            ok, reason = await budget.guard.can_afford(budget.today, worst)
            if not ok:
                budget.exhausted = reason
                raise AssessmentFailed(f"費用ガードにより停止: {reason}")
        try:
            gen = await asyncio.wait_for(llm.generate_json(prompt, RESPONSE_SCHEMA), timeout_s)
        except TimeoutError as e:
            raise AssessmentFailed(f"LLM の応答が {timeout_s:g} 秒以内に無い") from e
        except Exception as e:  # noqa: BLE001 - LLM の障害はその単位の UNKNOWN
            raise AssessmentFailed(f"LLM の呼出しに失敗: {type(e).__name__}: {e}") from e
        if budget.guard is not None:
            await budget.guard.record(
                budget.today, budget.run_id, llm.model, gen.input_tokens, gen.output_tokens
            )
        trials.append(parse_response(gen.text, params))
    items: list[ItemVerdict] = []
    for item in params.items:
        votes = [t[item.id][0] for t in trials]
        quote = next((t[item.id][1] for t in trials if t[item.id][0] and t[item.id][1]), None)
        items.append(
            ItemVerdict(item_id=item.id, outcome=outcome_of(votes), votes=votes, quote=quote)
        )
    return AssessmentRecord(key=key, unit_symbol=unit.symbol, items=items, created_at=now)


def _path_of(symbol: str) -> str:
    return symbol.split("::", 1)[0]


def _item_finding(
    rule: PolicyRule,
    item: LlmChecklistItem,
    verdict: ItemVerdict,
    unit: AssessmentUnit,
    key: AssessmentKey,
) -> Finding:
    fp = fingerprint(rule.id, unit.symbol, unit.unit_hash, item.id)
    yes = sum(1 for v in verdict.votes if v)
    n = len(verdict.votes)
    outcome = verdict.outcome
    if outcome == "issue":
        message = f"{rule.id}: 「{item.question}」に該当する（LLM {n} 回中 {yes} 回）"
        severity = "high" if rule.level == "mandatory" else "medium"
    elif outcome == "split":
        message = f"{rule.id}: 「{item.question}」の評価が割れた（LLM {n} 回中 {yes} 回が該当）"
        severity = "medium"
    else:
        message = f"{rule.id}: 「{item.question}」 — {NO_ISSUE_LABEL}"
        severity = "info"
    next_steps = (
        [
            "指摘どおりなら直す。問題が無ければ owner / approver が確定する"
            f"（PR コメント `/speclino attest {fp} {unit.unit_hash}`）"
        ]
        if outcome != "no_issue"
        else []
    )
    return Finding(
        id=f"llm:{fp}",
        requirement_id=f"{rule.id}:{_path_of(unit.symbol)}",
        behavior_id=BEHAVIOR_ID,
        verdict="REVIEW",
        category="conformant" if outcome == "no_issue" else "potential_violation",
        severity=severity,
        message=message,
        expected=item.question,
        actual=verdict.quote,
        evidence_refs=[
            f"{FINGERPRINT_REF}{fp}",
            f"{CONTENT_HASH_REF}{unit.unit_hash}",
            f"{ASSESSMENT_KEY_REF}{key_str(key)}",
        ],
        assumptions=["LLM による評価（機械検証ではない）。確定は人が行う"],
        claim_scope="persisted_state",
        code_refs=[SourceRef(snapshot_id="head", symbol=unit.symbol, path=_path_of(unit.symbol))],
        next_steps=next_steps,
        assessment_outcome=outcome,
    )


def unknown_finding(
    rule: PolicyRule, target: str, reason: str, *, suffix: str, path: str | None
) -> Finding:
    """LLM 支援で評価できなかった単位（またはルール全体: path=None）の UNKNOWN。"""
    return Finding(
        id=f"llm:{rule.id}:{suffix}:{hashlib.sha256(target.encode()).hexdigest()[:16]}",
        requirement_id=f"{rule.id}:{path}" if path else rule.id,
        behavior_id=BEHAVIOR_ID,
        verdict="UNKNOWN",
        category="unsupported_analysis",
        severity="high" if rule.level == "mandatory" else "medium",
        message=f"{rule.id}: LLM 支援の評価ができない（{target}）: {reason}",
        claim_scope="persisted_state",
        code_refs=[SourceRef(snapshot_id="head", symbol=target, path=path)] if path else [],
        next_steps=["LLM の設定・費用枠を確かめて再実行する（評価できるまでゲートは通らない）"],
    )


def _unit_unknown(
    rule: PolicyRule, unit: AssessmentUnit, reason: str, suffix: str = "error"
) -> Finding:
    return unknown_finding(rule, unit.symbol, reason, suffix=suffix, path=_path_of(unit.symbol))


def _broken_record(record: AssessmentRecord) -> str | None:
    """記録の各項目が TRIALS 回の票を持ち、区分が票と合っているか（書き換え・破損の検出）。"""
    for item in record.items:
        if len(item.votes) != TRIALS:
            return f"{item.item_id} の票が {len(item.votes)} 件"
        if item.outcome != outcome_of(item.votes):
            return f"{item.item_id} の区分 {item.outcome} が票と合わない"
    return None


async def assess(
    rule: PolicyRule,
    units: list[AssessmentUnit],
    *,
    llm: TextLlm | None,
    store: AssessmentStore,
    policy_revision: int,
    cost_guard: CostGuard | None = None,
    model: str | None = None,
    run_id: str | None = None,
    today: date | None = None,
    now: datetime | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    unassessable: Sequence[tuple[str, str]] = (),
    origin: str | None = None,
    call_budget: CallBudget | None = None,
) -> tuple[list[Finding], list[str]]:
    """ルール 1 つを単位ごとに評価し、(Finding, 使ったキー) を返す。verdict は常に REVIEW。

    `llm` が None なら記録だけを使う（replay）。記録の無いキーは UNKNOWN。その場合のモデル名は
    `model`（manifest のキーと同じもの）で、無ければ記録を引けないので全部 UNKNOWN。
    `unassessable`（(パスかシンボル, 理由)。読めない・一意に決まらない・大きすぎる・対象外の言語）
    はそれぞれ UNKNOWN。`origin` は規約の出所（キーのルール部分に入る）。`call_budget` は 1 run の
    呼出しの上限（ルール間で共有する）。3 回を最後まで呼べない単位は呼び始めず UNKNOWN。
    """
    params = checklist_params(rule)
    model_name = llm.model if llm is not None else model
    now = now or datetime.now(UTC)
    budget = _Budget(cost_guard, today or now.date(), run_id, call_budget)
    findings: list[Finding] = [
        unknown_finding(rule, target, reason, suffix="unassessable", path=_path_of(target))
        for target, reason in unassessable
    ]
    keys: list[str] = []
    ordered = sorted(units, key=lambda u: u.symbol)
    if len(ordered) > params.max_units:
        skipped = ordered[params.max_units :]
        ordered = ordered[: params.max_units]
        findings.append(
            unknown_finding(
                rule,
                rule.id,
                f"単位が上限 {params.max_units} を超えた（{len(skipped)} 件を評価していない。"
                f"最初: {skipped[0].symbol}）",
                suffix="over_limit",
                path=None,
            )
        )
    by_id = {i.id: i for i in params.items}
    for unit in ordered:
        if model_name is None:
            findings.append(_unit_unknown(rule, unit, "モデルが分からない"))
            continue
        key = assessment_key(
            rule, unit, model=model_name, policy_revision=policy_revision, origin=origin
        )
        record = await store.get(key)
        if record is not None and (broken := _broken_record(record)) is not None:
            findings.append(_unit_unknown(rule, unit, f"評価の記録が壊れている: {broken}"))
            continue
        if record is None:
            if llm is None:
                findings.append(
                    _unit_unknown(rule, unit, "評価の記録が無い（replay は LLM を呼ばない）")
                )
                continue
            if call_budget is not None and call_budget.used + TRIALS > call_budget.max_calls:
                findings.append(
                    _unit_unknown(
                        rule, unit, f"1 run の LLM 呼出しの上限 {call_budget.max_calls} 回に達した"
                    )
                )
                continue
            try:
                record = await _evaluate(
                    rule, params, unit, key, llm=llm, budget=budget, timeout_s=timeout_s, now=now
                )
            except (AssessmentFailed, ValidationError) as e:
                findings.append(_unit_unknown(rule, unit, str(e)))
                continue
            await store.put(record)
        keys.append(key_str(key))
        verdicts = {v.item_id: v for v in record.items}
        for item_id, item in by_id.items():
            verdict = verdicts.get(item_id)
            if verdict is None:
                findings.append(
                    _unit_unknown(rule, unit, f"記録に項目 {item_id} が無い", f"item-{item_id}")
                )
                continue
            findings.append(_item_finding(rule, item, verdict, unit, key))
    return findings, keys


__all__ = [
    "ASSESSMENT_KEY_REF",
    "BEHAVIOR_ID",
    "CONTENT_HASH_REF",
    "FINGERPRINT_REF",
    "NO_ISSUE_LABEL",
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA",
    "TRIALS",
    "AssessmentFailed",
    "AssessmentStore",
    "CallBudget",
    "DEFAULT_MAX_CALLS_PER_RUN",
    "key_str",
    "parse_key",
    "rule_key",
    "InMemoryAssessmentStore",
    "assess",
    "assessment_key",
    "build_prompt",
    "checklist_params",
    "fingerprint",
    "outcome_of",
    "parse_response",
    "prompt_version",
    "unknown_finding",
]
