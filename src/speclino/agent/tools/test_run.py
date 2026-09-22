"""test_run: 宣言済みテストを隔離環境で実行し、観測証拠として判定に反映する（エスカレーション③）。

- 実行できるのは Rule.tests に宣言されたテスト ID だけ（任意のコマンドは受けない）
- テスト・設定は base 由来、実装は head 由来（PoC 16 章 / codex 指摘 #3）
- assertion 失敗 → VIOLATED（observed_counterexample）。全て成功 → VERIFIED_IN_SCOPE
  （動的・実行経路の範囲、test_observation=OBSERVED）。
- error / skipped / timeout / ID 不在は判定を変えない
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from speclino.agent.gateway import ToolCall, ToolGateway, ToolRegistry
from speclino.agent.tools.context import InvestigationContext
from speclino.agent.tools.must_tools import _recount
from speclino.domain.finding import Category, Counterexample
from speclino.domain.spec_ir import EventRequirement, Rule
from speclino.trace.junit import TestOutcome, parse_junit

TIMEOUT_S = 120


def _observed_category(rule: Rule) -> Category:
    a = rule.assertion
    if isinstance(a, EventRequirement):
        if a.count.value == 0 and a.count.operator in ("eq", "le"):
            return "forbidden_effect"
        return "missing_effect"
    from speclino.domain.expr import refs_in

    refs = refs_in(a.expr)
    if any(r.namespace == "after" for r in refs):
        return "state_mismatch"
    if any(r.namespace == "outcome" for r in refs):
        return "outcome_mismatch"
    return "constraint_mismatch"


def _find_rule(ctx: InvestigationContext, rule_id: str) -> tuple[Rule, str] | None:
    for bid, spec in ctx.specs.items():
        for r in spec.rules:
            if r.id == rule_id:
                return r, bid
    return None


def test_run(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(args.get("rule_id", "")).strip()
    found = _find_rule(ctx, rule_id)
    if found is None:
        return {"error": "unknown_rule", "rule_id": rule_id}
    rule, behavior_id = found
    if not rule.tests:
        return {
            "error": "no_declared_tests",
            "rule_id": rule_id,
            "next": "Spec IR の tests に宣言する",
        }
    if ctx.sandbox is None or ctx.base_root is None:
        return {"error": "sandbox_unavailable", "note": "この環境では隔離実行ができない"}
    result = ctx.sandbox.run_tests(ctx.repo_root, ctx.base_root, list(rule.tests), TIMEOUT_S)
    outcomes: list[TestOutcome] = parse_junit(result.junit_xml) if result.junit_xml else []
    by_id = {o.test_id: o for o in outcomes}
    statuses = {t: (by_id[t].status if t in by_id else "missing") for t in rule.tests}
    ctx.record(
        "tool.test_run",
        {
            "rule_id": rule_id,
            "runner": result.runner,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "statuses": statuses,
        },
    )
    current = next((f for f in ctx.findings.findings if f.requirement_id == rule_id), None)
    if current is None:
        return {"error": "no_finding", "rule_id": rule_id}
    if result.timed_out or any(s in ("missing", "error", "skipped") for s in statuses.values()):
        updated = current.model_copy(
            update={
                "next_steps": [
                    *current.next_steps,
                    "宣言済みテストが実行できなかった（error / skipped / timeout / ID 不在）",
                ]
            }
        )
        verdict_note = "unchanged"
    elif any(s == "failed" for s in statuses.values()):
        failed = [t for t, s in statuses.items() if s == "failed"]
        updated = current.model_copy(
            update={
                "verdict": "VIOLATED",
                "category": _observed_category(rule),
                "severity": "high" if rule.level == "mandatory" else "medium",
                "message": f"宣言済みテストが失敗した: {', '.join(failed)}",
                "counterexample": Counterexample(
                    kind="observed",
                    description="; ".join(f"{t}: {by_id[t].message}" for t in failed)[:1000],
                ),
                "evidence_refs": [*current.evidence_refs, *failed],
                "test_observation": "CONTRADICTED",
                "analysis_scope": "動的（base のテストを head の実装で実行）",
                "next_steps": [],
            }
        )
        verdict_note = "VIOLATED"
    else:
        updated = current.model_copy(
            update={
                "verdict": "VERIFIED_IN_SCOPE",
                "category": "conformant",
                "severity": "info",
                "message": f"宣言済みテストが成功した: {', '.join(rule.tests)}",
                "evidence_refs": [*current.evidence_refs, *rule.tests],
                "test_observation": "OBSERVED",
                "analysis_scope": "動的（実行した入力・経路の範囲に限る。全経路の適合ではない）",
                "next_steps": [],
            }
        )
        verdict_note = "VERIFIED_IN_SCOPE"
    ctx.findings = _recount(
        ctx.findings.model_copy(
            update={
                "findings": [updated if f.id == current.id else f for f in ctx.findings.findings]
            }
        )
    )
    return {
        "rule_id": rule_id,
        "runner": result.runner,
        "statuses": statuses,
        "verdict": verdict_note,
        "stdout_tail": result.stdout_tail[-800:],
    }


def register_test_run(registry: ToolRegistry, ctx: InvestigationContext) -> None:
    registry.register("test_run", lambda a: test_run(ctx, a))


def build_test_run_fn(gateway_ref: Callable[[], ToolGateway]) -> Callable[..., Any]:
    async def test_run(rule_id: str) -> dict[str, Any]:
        """要求 rule_id に宣言された spec テストを隔離環境（ネットワーク無し）で実行し、
        観測証拠にする。"""
        return await asyncio.to_thread(
            lambda: gateway_ref().invoke(ToolCall("test_run", {"rule_id": rule_id})).to_dict()
        )

    return test_run


__all__ = ["build_test_run_fn", "register_test_run", "test_run"]
