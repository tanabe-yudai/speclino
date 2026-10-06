"""`llm.checklist` の登録（S70、設計 6.2）。

この検査器は LLM を呼ばない（`verifiers/` は判定の層で、LLM は `services/llm_policies.py`）。
worker が run の前に評価した Finding（ルール ID → Finding の列）を受け取り、そのまま返すだけ。
この run で評価していないルール（CLI のローカル実行・LLM の設定が無い worker）は UNKNOWN。
評価したが単位が 1 つも無いルールは `on_missing_input`（skipped なら何も出さない、それ以外は
UNKNOWN。LLM 支援は VIOLATED を出さない）。
"""

from __future__ import annotations

from collections.abc import Mapping

from speclino.domain.finding import Finding
from speclino.domain.policy import LLM_CHECKLIST_VERIFIER_ID, PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_VERSION = "1"


class LlmChecklistVerifier:
    id = LLM_CHECKLIST_VERIFIER_ID
    version = VERIFIER_VERSION

    def __init__(self, results: Mapping[str, list[Finding]] | None = None) -> None:
        self._results = results

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        del snapshot  # 単位の取り出しと評価は worker が済ませている
        if self._results is None or rule.id not in self._results:
            return [
                _unknown(rule, "この run では LLM 支援の評価を行っていない（LLM の設定が無い）")
            ]
        found = self._results[rule.id]
        if found:
            return list(found)
        if rule.on_missing_input == "skipped":
            return []
        return [_unknown(rule, "評価する単位（関数・文字列・エンドポイント）が見つからない")]


def _unknown(rule: PolicyRule, reason: str) -> Finding:
    return Finding(
        id=f"policy:{rule.id}:llm_not_assessed",
        requirement_id=rule.id,
        behavior_id=f"policy:{LLM_CHECKLIST_VERIFIER_ID}",
        verdict="UNKNOWN",
        category="unsupported_analysis",
        severity="high" if rule.level == "mandatory" else "medium",
        message=f"{rule.id}: {reason}",
        claim_scope="persisted_state",
        next_steps=["SpecLino のサーバ（LLM 支援を有効にした worker）で検査する"],
    )


__all__ = ["VERIFIER_VERSION", "LlmChecklistVerifier"]
