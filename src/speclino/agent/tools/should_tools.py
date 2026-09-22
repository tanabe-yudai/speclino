"""Should ツール: contract_lookup / human_ask（人への構造化質問、FR 015）。

human_ask は「事前に構造化した選択肢」だけを扱う。回答は承認者が `/speclino answer Q-x <選択肢>` で
行い、子 run が同じ版一式で再開する。仕様の意味変更は回答で解決させず、正本の修正へ誘導する。
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from typing import Any

from speclino.agent.gateway import ToolCall, ToolGateway, ToolRegistry
from speclino.agent.tools.context import InvestigationContext

QUESTION_KINDS = ("binding", "contract")
MAX_QUESTIONS = 3
MAX_OPTIONS = 5


def contract_lookup(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """シンボルに対応する契約・操作束縛を探す。無ければ名前の近い候補を返す（決定的）。"""
    symbol = str(args.get("symbol", "")).strip()
    if not symbol:
        return {"error": "empty_symbol"}
    ops = {op_id: ob.symbol for op_id, ob in ctx.bindings.operations.items()}
    exact = [op_id for op_id, sym in ops.items() if sym == symbol]
    contracts = [c.id for c in ctx.contracts.values() if c.symbol == symbol]
    names = list(ops.values()) + [c.symbol for c in ctx.contracts.values()]
    tail = symbol.rsplit(".", 1)[-1]
    similar = sorted(
        {
            op_id
            for op_id, sym in ops.items()
            if difflib.SequenceMatcher(None, tail, sym.rsplit(".", 1)[-1]).ratio() >= 0.5
        }
    )
    ctx.record(
        "tool.contract_lookup",
        {"symbol": symbol, "exact": exact, "contracts": contracts, "similar": similar},
    )
    return {
        "symbol": symbol,
        "bound_operations": exact,
        "contracts": contracts,
        "similar_operations": similar,
        "known_symbols": names,
        "untrusted": False,
    }


def human_ask(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """承認者への構造化質問を登録する。run は回答待ちになる（ゲートは通らない）。"""
    kind = str(args.get("kind", ""))
    requirement_id = str(args.get("requirement_id", "")).strip() or None
    question = str(args.get("question", "")).strip()[:500]
    options = [str(o)[:200] for o in (args.get("options") or [])][:MAX_OPTIONS]
    symbol = str(args.get("symbol", "")).strip() or None
    if kind not in QUESTION_KINDS:
        return {"error": "invalid_kind", "allowed": list(QUESTION_KINDS)}
    if not question or len(options) < 2:
        return {"error": "need_question_and_2_options"}
    if len(ctx.questions) >= MAX_QUESTIONS:
        return {"error": "question_limit", "max": MAX_QUESTIONS}
    if kind == "binding":
        valid = set(ctx.bindings.operations) | {"none"}
        bad = [o for o in options if o not in valid]
        if bad or not symbol:
            return {
                "error": "binding_options_must_be_operation_ids",
                "invalid": bad,
                "operations": sorted(ctx.bindings.operations),
            }
    entry = {
        "kind": kind,
        "requirement_id": requirement_id,
        "symbol": symbol,
        "question": question,
        "options": options,
    }
    ctx.questions.append(entry)
    ctx.record("question", entry)
    return {"registered": True, "n": len(ctx.questions), "note": "回答は承認者が PR コメントで行う"}


def register_should_tools(registry: ToolRegistry, ctx: InvestigationContext) -> None:
    registry.register("contract_lookup", lambda a: contract_lookup(ctx, a))
    registry.register("human_ask", lambda a: human_ask(ctx, a))


def build_should_tool_fns(gateway_ref: Callable[[], ToolGateway]) -> list[Callable[..., Any]]:
    def contract_lookup(symbol: str) -> dict[str, Any]:
        """完全修飾シンボルに対応する契約・操作束縛と、名前の近い候補を返す。"""
        return gateway_ref().invoke(ToolCall("contract_lookup", {"symbol": symbol})).to_dict()

    def human_ask(
        kind: str,
        question: str,
        options: list[str],
        requirement_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """承認者に構造化質問を出す。kind は binding（symbol を options のどの操作に対応づけるか。
        options は操作 ID か "none"）または contract。回答までゲートは通らない。最大 3 件。"""
        return (
            gateway_ref()
            .invoke(
                ToolCall(
                    "human_ask",
                    {
                        "kind": kind,
                        "question": question,
                        "options": options,
                        "requirement_id": requirement_id,
                        "symbol": symbol,
                    },
                )
            )
            .to_dict()
        )

    return [contract_lookup, human_ask]
