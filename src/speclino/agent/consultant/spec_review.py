"""A2A のスキル `spec_review`（S59）: 仕様の下書きから Spec IR の候補・診断・修正案を返す。

曖昧な点は聞き返す（入力待ち）。相手の答えは仕様の本文に混ぜず、抽出のプロンプトの後ろに
「補足」の節として足すだけにする（`spec_content_hash` と参照の検査は元の本文のまま）。
候補を出すだけで承認・判定はしない。取り込みと承認は PR で人が行う。
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from a2a.helpers import new_data_part, new_text_part
from pydantic import ValidationError

from speclino.a2a_server.executor import STATE_METADATA_KEY, SkillCall
from speclino.agent.budget import BudgetState
from speclino.agent.consultant.tools import (
    ConsultDeps,
    ConsultTools,
    consult_permissions,
    guarded,
)
from speclino.agent.gateway import ToolGateway, ToolRegistry
from speclino.domain.bindings import Bindings
from speclino.domain.domain_spec import DomainSpec
from speclino.ports.text_llm import JsonGeneration, TextLlm
from speclino.services.spec_pr_bot import MAX_SPEC_CHARS, worst_case_cost
from speclino.spec.diagnostics import Diagnostic
from speclino.spec.spec_extractor import (
    ExtractionResult,
    build_prompt,
    parse_proposal,
    proposal_schema,
)

MAX_REVIEW_ROUNDS = 2  # 曖昧な点を聞き返す回数の上限
_MAX_BEHAVIOR_ASKS = 2  # behavior を聞く回数（2 回目の答えも候補に無ければ失敗）
_ANSWERS_HEADING = "相手のエージェントの回答（仕様の本文ではない。要求を足す根拠にしない）"
_DISCLAIMER = "取り込みと承認は PR で人が行います。この結果は検査器の判定ではありません。"

REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "before": {"type": "string"},
                    "after": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["requirement_id", "before", "after", "reason"],
            },
        }
    },
    "required": ["edits"],
}
_REVISION_RULES = """\
あなたは仕様書の曖昧な語句を、確認の答えに沿って書き直す案を出す。判断や承認はしない。
規則:
- 本文の語句をそのまま before に引用し、after に置き換え案を書く
- 新しい要求を作らない。要求 ID を足さない
- 判定・承認・「違反しない」のような結論を書かない
- 答えが無い・答えで確定できない語句は edits に入れない
"""


class _Stop(Exception):
    """手順を失敗で終える（理由は相手に返してよい）。"""


class _BudgetRefused(_Stop):
    """費用枠の予約を断られた（LLM は呼んでいない）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"費用ガードにより停止: {reason}")
        self.reason = reason


async def run_spec_review(call: SkillCall, deps: ConsultDeps) -> None:
    """仕様のレビュー。入力待ち・完了・失敗のどれかで終える。監査は executor が残す。"""
    perms = consult_permissions()
    gateway = ToolGateway(
        perms, ToolRegistry(), call.audit, BudgetState(perms.max_tool_calls, perms.max_seconds)
    )
    try:
        await _review(call, deps, ConsultTools(deps, call.identity, gateway), gateway)
    except _Stop as e:
        await call.updater.failed(call.updater.new_agent_message([new_text_part(str(e))]))


async def _review(
    call: SkillCall, deps: ConsultDeps, tools: ConsultTools, gateway: ToolGateway
) -> None:
    req, updater = call.request, call.updater
    markdown = req.text
    if not req.repo:
        raise _Stop("repo（owner/name）を指定してください")
    if not markdown.strip():
        raise _Stop("仕様の本文が空です")
    if len(markdown) > MAX_SPEC_CHARS:
        raise _Stop(f"仕様が大きすぎる（{len(markdown)} 文字、上限 {MAX_SPEC_CHARS} 文字）")
    if deps.spec_llm is None:
        raise _Stop("仕様の抽出に使う LLM が設定されていない")
    llm = deps.spec_llm
    await updater.start_work()

    context = await tools.fetch_spec_context(req.repo, "HEAD", truncate=False)
    if "error" in context:
        raise _Stop(f"仕様の語彙を読めません: {context['error']}")
    if not context.get("domain") or not context.get("bindings"):
        raise _Stop("`.speclino/` の語彙が無い（domain.yaml / bindings.yaml）")
    domain = DomainSpec.model_validate(context["domain"])
    bindings = Bindings.model_validate(context["bindings"])

    state = dict(call.state or {})
    behavior = await _resolve_behavior(call, state, context, bindings)
    if behavior is None:
        return  # 入力待ちにした

    round_ = int(state.get("round", 0))
    questions: list[list[str]] = [list(q) for q in state.get("questions", [])]
    answers: list[str] = [str(a) for a in state.get("answers", [])]
    if round_ > 0 and call.reply is not None and state.get("need") != "behavior":
        answers.append(call.reply)

    prompt = build_prompt(markdown, behavior, domain, bindings)
    if questions:
        prompt += "\n\n" + _answers_section(questions, answers)
    if len(prompt) > 2 * MAX_SPEC_CHARS:
        raise _Stop(f"補足を含めた入力が大きすぎる（{len(prompt)} 文字）")

    async def extract() -> dict[str, Any]:
        gen = await llm.generate_json(prompt, proposal_schema())
        return {
            "text": gen.text,
            "input_tokens": gen.input_tokens,
            "output_tokens": gen.output_tokens,
        }

    try:
        out = await _metered(deps, llm, prompt, gateway, {"behavior": behavior}, extract)
    except _Stop:
        raise
    except Exception as e:
        raise _Stop(f"仕様の抽出に失敗しました: {type(e).__name__}") from e
    result = parse_proposal(out["text"], behavior, markdown, domain, bindings, spec_path=req.path)
    if any(d.code == "E_CANDIDATE_INVALID" and d.path == "proposal" for d in result.errors):
        # 抽出の応答そのものが壊れている。空の候補を結果として見せず、修正案も作らない
        raise _Stop("仕様の抽出結果の形式が不正でした")

    asks = _ambiguity_questions(result)
    if asks and round_ < MAX_REVIEW_ROUNDS:
        new_state = {
            "round": round_ + 1,
            "behavior": behavior,
            "questions": [*questions, asks],
            "answers": answers,
        }
        text = "\n".join(
            [
                "仕様の曖昧な点を確認させてください"
                "（答えは補足として扱い、仕様の本文には混ぜません）。",
                *asks,
                "番号を付けて答えてください。",
            ]
        )
        await _ask(call, text, new_state)
        return

    diff, revision_note = await _revision(
        deps, llm, gateway, markdown, req.path, questions, answers, result
    )
    await _emit(call, result, context, diff, revision_note, asks)


async def _resolve_behavior(
    call: SkillCall, state: dict[str, Any], context: dict[str, Any], bindings: Bindings
) -> str | None:
    """behavior を決める。決まらなければ入力待ちにして None。"""
    candidates = sorted(bindings.behaviors)
    if state.get("behavior"):
        return str(state["behavior"])
    if call.request.behavior:
        if call.request.behavior not in bindings.behaviors:
            raise _Stop(
                f"behavior {call.request.behavior} は bindings に無い。"
                f"候補: {', '.join(candidates)}"
            )
        return call.request.behavior
    if state.get("need") == "behavior":
        answer = (call.reply or "").strip()
        if answer in bindings.behaviors:
            return answer
        asks = int(state.get("asks", 1))
        if asks >= _MAX_BEHAVIOR_ASKS:
            raise _Stop(f"behavior が候補にありません: {answer}")
        await _ask_behavior(call, candidates, asks + 1)
        return None
    if call.request.path:
        found = {
            r.get("behavior_id")
            for r in context.get("rules", [])
            if r.get("spec") == call.request.path and r.get("behavior_id") in bindings.behaviors
        }
        if len(found) == 1:
            return str(found.pop())
    if len(candidates) == 1:
        return candidates[0]
    await _ask_behavior(call, candidates, 1)
    return None


async def _ask_behavior(call: SkillCall, candidates: list[str], asks: int) -> None:
    text = f"behavior を指定してください。候補: {', '.join(candidates)}"
    await _ask(call, text, {"round": 0, "need": "behavior", "asks": asks})


async def _ask(call: SkillCall, text: str, state: dict[str, Any]) -> None:
    updater = call.updater
    await updater.requires_input(
        updater.new_agent_message([new_text_part(text)], metadata={STATE_METADATA_KEY: state})
    )


def _ambiguity_questions(result: ExtractionResult) -> list[str]:
    ambiguous = [d for d in result.warnings if d.code == "W_AMBIGUOUS"]
    return [
        f"{i}. {d.rule_id or '（要求 ID なし）'} の{d.message}" for i, d in enumerate(ambiguous, 1)
    ]


def _answers_section(questions: list[list[str]], answers: list[str]) -> str:
    lines = [f"{_ANSWERS_HEADING}:", "<<<"]
    for i, qs in enumerate(questions):
        lines.append(f"質問（{i + 1} 回目）:")
        lines.extend(qs)
        lines.append(f"回答: {answers[i] if i < len(answers) else '（回答なし）'}")
    lines.append(">>>")
    return "\n".join(lines)


def _worst(deps: ConsultDeps, llm: TextLlm, prompt: str) -> Decimal:
    if deps.cost_guard is None:
        return Decimal(0)
    return worst_case_cost(deps.cost_guard, llm, prompt)


async def _metered(
    deps: ConsultDeps,
    llm: TextLlm,
    prompt: str,
    gateway: ToolGateway,
    args: dict[str, Any],
    fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Gemini 呼出しを 1 回、費用枠の予約・門番（spec_extract）・精算で包む。

    予約できなければ `_BudgetRefused`（LLM は呼ばない）。予約の後は例外・取り消しを含む
    どの終わり方でも、予約と同じ日に精算する（実費が分からなければ (0, 0) で戻す）。
    """
    budget = deps.budget
    reservation = None
    if budget is not None:  # ローカルとテストは None。本番は常に設定する
        reservation, reason = await budget.reserve(
            deps.today(), llm.model, _worst(deps, llm, prompt)
        )
        if reservation is None:
            raise _BudgetRefused(reason)
    tokens = (0, 0)
    try:
        out = await guarded(gateway, "spec_extract", args, fn, truncate=False)
        tokens = (int(out.get("input_tokens", 0)), int(out.get("output_tokens", 0)))
    finally:
        if budget is not None and reservation is not None:
            await budget.settle(reservation, *tokens)
    if out.get("error"):  # 門番が拒否した（実行していない）
        raise _Stop(f"門番が拒否しました: {out.get('reason', out['error'])}")
    return out


async def _revision(
    deps: ConsultDeps,
    llm: TextLlm,
    gateway: ToolGateway,
    markdown: str,
    path: str | None,
    questions: list[list[str]],
    answers: list[str],
    result: ExtractionResult,
) -> tuple[str, str]:
    """修正案（unified diff）と要約に書く一言。費用・失敗で作れなくても抽出の結果は返す。"""
    diags = [f"- {d.code} {d.rule_id or ''}: {d.message}" for d in result.errors + result.warnings]
    prompt = "\n".join(
        [
            _REVISION_RULES,
            "仕様 Markdown:",
            "<<<",
            markdown,
            ">>>",
            "",
            _answers_section(questions, answers) if questions else "確認の質問と答え: なし",
            "",
            "診断:",
            *(diags or ["なし"]),
        ]
    )

    async def revise() -> dict[str, Any]:
        gen: JsonGeneration = await llm.generate_json(prompt, REVISION_SCHEMA)
        return {
            "text": gen.text,
            "input_tokens": gen.input_tokens,
            "output_tokens": gen.output_tokens,
        }

    try:
        out = await _metered(deps, llm, prompt, gateway, {"purpose": "revision"}, revise)
        edits = _parse_edits(out["text"])
    except _BudgetRefused as e:
        return "", f"費用ガードにより修正案を作りませんでした: {e.reason}"
    except _Stop as e:
        return "", f"修正案を作りませんでした: {e}"
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        return "", f"修正案の形式が不正なため捨てました: {type(e).__name__}"
    except Exception as e:
        return "", f"修正案の生成に失敗しました: {type(e).__name__}"
    diff, skipped = render_revision_diff(markdown, edits, path=path)
    note = f"修正案: {len(edits) - len(skipped)} 件。" if diff else "修正案はありません。"
    if skipped:
        note += " 本文に見つからず捨てた編集: " + "; ".join(skipped)
    return diff, note


def _parse_edits(text: str) -> list[dict[str, str]]:
    data = json.loads(text)
    edits = data.get("edits") if isinstance(data, dict) else None
    if not isinstance(edits, list):
        raise ValueError("edits が無い")
    out: list[dict[str, str]] = []
    for e in edits:
        if not isinstance(e, dict):
            raise ValueError("edit が object でない")
        out.append({k: str(e.get(k, "")) for k in ("requirement_id", "before", "after", "reason")})
    return out


def render_revision_diff(
    markdown: str, edits: list[dict[str, str]], *, path: str | None = None
) -> tuple[str, list[str]]:
    """編集（before → after）を本文に当てた unified diff と、当てられず捨てた編集の理由。

    before は本文での最初の 1 回だけを置き換える。本文に無い before は捨てる。
    """
    revised = markdown
    skipped: list[str] = []
    for e in edits:
        before, after = e.get("before", ""), e.get("after", "")
        rid = e.get("requirement_id") or "?"
        if not before or before not in revised:
            skipped.append(f"{rid}: 「{before}」が本文に見つからない")
            continue
        revised = revised.replace(before, after, 1)
    if revised == markdown:
        return "", skipped
    name = path or "spec.md"
    diff = difflib.unified_diff(
        markdown.splitlines(keepends=True),
        revised.splitlines(keepends=True),
        fromfile=f"a/{name}",
        tofile=f"b/{name}",
    )
    return "".join(diff), skipped


async def _emit(
    call: SkillCall,
    result: ExtractionResult,
    context: dict[str, Any],
    diff: str,
    revision_note: str,
    remaining: list[str],
) -> None:
    updater = call.updater
    diagnostics: list[Diagnostic] = result.errors + result.warnings
    existing = {r.get("id"): r for r in context.get("rules", [])}
    conflicts = {
        "overwrites": [
            {
                "id": rule.id,
                "existing_behavior": existing[rule.id].get("behavior_id"),
                "existing_spec": existing[rule.id].get("spec"),
            }
            for rule in result.spec.rules
            if rule.id in existing
        ],
        "policies": [
            {"id": p.get("id"), "title": p.get("title"), "level": p.get("level")}
            for p in context.get("policies", [])
        ],
    }
    await updater.add_artifact(
        [new_data_part(result.spec.model_dump(mode="json"))], name="spec-ir-candidate.json"
    )
    await updater.add_artifact(
        [new_data_part([d.model_dump(mode="json") for d in diagnostics])], name="diagnostics.json"
    )
    await updater.add_artifact([new_data_part(conflicts)], name="conflicts.json")
    if diff:
        await updater.add_artifact([new_text_part(diff)], name="revision.diff")
    summary = _summary(result, conflicts, revision_note, remaining)
    await updater.add_artifact([new_text_part(summary)], name="summary.md")
    await updater.complete(updater.new_agent_message([new_text_part(summary)]))


def _summary(
    result: ExtractionResult, conflicts: dict[str, Any], revision_note: str, remaining: list[str]
) -> str:
    lines = [
        "## SpecLino: 仕様レビュー（Spec IR の候補）",
        "",
        f"- behavior: `{result.spec.behavior_id}`",
        f"- 候補の要求: {len(result.spec.rules)} 件",
        f"- エラー: {len(result.errors)} 件 / 警告: {len(result.warnings)} 件",
        f"- IR にできなかった要求: {len(result.spec.unextracted)} 件",
        f"- 既存の要求を上書きする候補: {len(conflicts['overwrites'])} 件",
        f"- 照らし合わせる規約: {len(conflicts['policies'])} 件"
        "（反するかどうかは判定していません）",
        f"- {revision_note}",
    ]
    if remaining:
        lines += ["", "### 残った質問", "", *remaining]
    lines += ["", f"> {_DISCLAIMER}"]
    return "\n".join(lines)


__all__ = ["MAX_REVIEW_ROUNDS", "REVISION_SCHEMA", "render_revision_diff", "run_spec_review"]
