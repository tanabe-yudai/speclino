"""A2A のスキル `spec_suggest`（S67）: 仕様の下書きの書き換えの提案を返す。

返すのは本文の置換の提案（`suggestions.json`）と要約だけ。IR の候補も判定も返さない。
本文から IR を作るのは決定的な parser（`compile_spec`）で、提案は当てた本文をその parser で
検証し直した結果を添える。採否・保存・承認は人が行う。
LLM の呼出しは門番（ツール名 `spec_suggest`）と日次の費用枠を通す。
"""

from __future__ import annotations

import asyncio
from typing import Any

from a2a.helpers import new_data_part, new_text_part

from speclino.a2a_server.executor import SkillCall
from speclino.agent.budget import BudgetState
from speclino.agent.consultant.tools import ConsultDeps, consult_permissions, guarded
from speclino.agent.gateway import ToolGateway, ToolRegistry
from speclino.domain.knowledge import SuggestionResult
from speclino.services.spec_suggest import SuggestRefused, suggest
from speclino.spec.uml import compile_spec

_DISCLAIMER = (
    "提案は本文の書き換えの案だけです。採否は人が 1 件ずつ決め、保存と承認も人が行います。"
    "この結果は検査器の判定ではありません。"
)


class _Stop(Exception):
    """手順を失敗で終える（理由は相手に返してよい）。"""


async def run_spec_suggest(call: SkillCall, deps: ConsultDeps) -> None:
    """提案を返して完了するか、理由を添えて失敗で終える。監査は executor が残す。"""
    perms = consult_permissions()
    gateway = ToolGateway(
        perms, ToolRegistry(), call.audit, BudgetState(perms.max_tool_calls, perms.max_seconds)
    )
    try:
        await _suggest(call, deps, gateway)
    except _Stop as e:
        await call.updater.failed(call.updater.new_agent_message([new_text_part(str(e))]))


async def _suggest(call: SkillCall, deps: ConsultDeps, gateway: ToolGateway) -> None:
    markdown = call.request.text
    if not markdown.strip():
        raise _Stop("仕様の本文が空です")
    llm, budget, guard = deps.spec_llm, deps.budget, deps.cost_guard
    if llm is None:
        raise _Stop("提案に使う LLM が設定されていない")
    if budget is None or guard is None:
        raise _Stop("費用枠が設定されていないため、提案を作れません")
    await call.updater.start_work()
    parsed = await asyncio.to_thread(compile_spec, markdown, domain=None, legacy_ir=[])
    diagnostics = parsed.diagnostics
    suggestions: list[SuggestionResult] = []

    async def run() -> dict[str, Any]:
        # 監査に残るのは返した dict だけ。本文の断片を残さないよう件数だけを返す
        try:
            suggestions.extend(
                await suggest(
                    markdown,
                    diagnostics,
                    llm=llm,
                    budget=budget,
                    cost_guard=guard,
                    today=deps.today(),
                )
            )
        except SuggestRefused as e:
            return {"refused": str(e)}
        return {"count": len(suggestions)}

    out = await guarded(gateway, "spec_suggest", {"chars": len(markdown)}, run, truncate=False)
    if out.get("error"):  # 門番が拒否した（実行していない）
        raise _Stop(f"門番が拒否しました: {out.get('reason', out['error'])}")
    if "refused" in out:
        raise _Stop(str(out["refused"]))
    errors_before = sum(1 for d in diagnostics if d.severity == "error")
    updater = call.updater
    await updater.add_artifact(
        [new_data_part([s.model_dump(mode="json") for s in suggestions])],
        name="suggestions.json",
    )
    summary = _summary(errors_before, suggestions)
    await updater.add_artifact([new_text_part(summary)], name="summary.md")
    await updater.complete(updater.new_agent_message([new_text_part(summary)]))


def _summary(errors_before: int, suggestions: list[SuggestionResult]) -> str:
    lines = [
        "## SpecLino: 仕様の書き換えの提案",
        "",
        f"- 今の本文のエラー: {errors_before} 件",
        f"- 提案: {len(suggestions)} 件",
    ]
    for i, s in enumerate(suggestions, 1):
        state = "変換できる" if s.after.compiles else f"エラー {s.after.errors_after} 件"
        lines.append(f"  {i}. 文字 {s.start}–{s.end}: {s.reason}（当てた後: {state}）")
    lines += ["", f"> {_DISCLAIMER}"]
    return "\n".join(lines)


__all__ = ["run_spec_suggest"]
