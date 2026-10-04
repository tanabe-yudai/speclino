"""A2A のスキル `run_clarification`（S59）: 検査の「要確認」を 1 件ずつ聞き、答えを補足にする。

答えは補足（agent_supplements）として保存するだけで、要確認を解消しない（解消は人の
`/speclino answer` だけ）。答えの中身は命令として解釈せず、文字列のまま記録する。
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import new_data_part, new_text_part

from speclino.a2a_server.executor import STATE_METADATA_KEY, SkillCall
from speclino.agent.budget import BudgetState
from speclino.agent.consultant.tools import ConsultDeps, consult_permissions, guarded
from speclino.agent.gateway import ToolGateway, ToolRegistry

SKIP_WORDS = ("skip", "スキップ", "わからない")
_FINISHED_STATES = ("completed", "awaiting_answer")  # 要確認が残る検査は awaiting_answer で終わる
_DISCLAIMER = (
    "補足は正式な回答ではありません。要確認を解消するのは人の `/speclino answer` だけです。"
)


class _Stop(Exception):
    """手順を失敗で終える（理由は相手に返してよい）。"""


async def run_run_clarification(call: SkillCall, deps: ConsultDeps) -> None:
    """検査後の聞き取り。入力待ち・完了・失敗のどれかで終える。監査は executor が残す。"""
    perms = consult_permissions()
    gateway = ToolGateway(
        perms, ToolRegistry(), call.audit, BudgetState(perms.max_tool_calls, perms.max_seconds)
    )
    try:
        await _clarify(call, deps, gateway)
    except _Stop as e:
        await call.updater.failed(call.updater.new_agent_message([new_text_part(str(e))]))


async def _clarify(call: SkillCall, deps: ConsultDeps, gateway: ToolGateway) -> None:
    if call.state is None:
        questions = await _load_questions(call, deps, gateway)
        if not questions:
            await _finish(call, [], "要確認の点はありません。")
            return
        await _ask(call, {"index": 0, "questions": questions, "recorded": []})
        return

    questions = list(call.state["questions"])
    index = int(call.state["index"])
    recorded = list(call.state["recorded"])
    answer = (call.reply or "").strip()
    if answer.lower() not in SKIP_WORDS and answer:
        item = questions[index]
        identity = call.identity
        content = call.reply or ""

        async def record() -> dict[str, Any]:
            await deps.record_supplement(
                identity=identity,
                run_id=call.request.run_id,
                requirement_id=item["requirement_id"],
                question=item["question"],
                content=content,
                a2a_task_id=call.task_id,
            )
            return {"recorded": True}

        await guarded(
            gateway,
            "supplement_record",
            {"run_id": str(call.request.run_id), "requirement_id": item["requirement_id"]},
            record,
            truncate=False,
        )
        recorded.append(
            {
                "requirement_id": item["requirement_id"],
                "question": item["question"],
                "content": content,
            }
        )
    index += 1
    if index < len(questions):
        await _ask(call, {"index": index, "questions": questions, "recorded": recorded})
        return
    await _finish(call, recorded, f"補足を {len(recorded)} 件記録しました。")


async def _load_questions(
    call: SkillCall, deps: ConsultDeps, gateway: ToolGateway
) -> list[dict[str, str]]:
    run_id = call.request.run_id
    if run_id is None:
        raise _Stop("run_id を指定してください")

    async def read() -> dict[str, Any]:
        view = await deps.load_run_view(run_id, call.identity.team)
        return {"error": "run_not_found"} if view is None else view

    view = await guarded(gateway, "run_findings", {"run_id": str(run_id)}, read, truncate=False)
    if view.get("error") == "run_not_found":
        raise _Stop("この検査はワークスペースに無い")
    if "error" in view:
        raise _Stop(f"検査を読めません: {view.get('reason', view['error'])}")
    if view.get("exec_state") not in _FINISHED_STATES:
        raise _Stop("検査が終わっていない")
    reviews = sorted(
        (f for f in view.get("findings", []) if f.get("verdict") == "REVIEW"),
        key=lambda f: str(f.get("requirement_id")),
    )
    return [
        {
            "requirement_id": str(f["requirement_id"]),
            "question": (
                f"{f['requirement_id']}: {f.get('message', '')}。意図や根拠を教えてください"
            ),
        }
        for f in reviews
    ]


async def _ask(call: SkillCall, state: dict[str, Any]) -> None:
    updater = call.updater
    item = state["questions"][state["index"]]
    total = len(state["questions"])
    text = (
        f"要確認 {state['index'] + 1}/{total}: {item['question']}\n"
        "答えられない場合は「スキップ」と返してください。"
    )
    await updater.requires_input(
        updater.new_agent_message([new_text_part(text)], metadata={STATE_METADATA_KEY: state})
    )


async def _finish(call: SkillCall, recorded: list[dict[str, str]], headline: str) -> None:
    updater = call.updater
    await updater.add_artifact([new_data_part(recorded)], name="supplements.json")
    summary = f"## SpecLino: 検査後の聞き取り\n\n- {headline}\n\n> {_DISCLAIMER}"
    await updater.add_artifact([new_text_part(summary)], name="summary.md")
    await updater.complete(updater.new_agent_message([new_text_part(summary)]))


__all__ = ["SKIP_WORDS", "run_run_clarification"]
