"""深掘りの評価の判定（実 Gemini の代わりに台本の LLM）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from speclino.eval.chat_depth import DEFAULT_CASES, DepthCase, load_cases, render_markdown, run_case
from speclino.ports.text_llm import JsonGeneration

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = [{"question": "誰が使いますか？", "options": ["開発者", "レビュアー"]}]
PROPOSAL = {"body": "# 指示\n"}


class ScriptedLlm:
    model = "scripted"

    def __init__(self, reply: dict[str, Any] | None) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        if self.reply is None:
            raise ValueError("bad request")
        return JsonGeneration(json.dumps(self.reply, ensure_ascii=False))


def _case(expect: str) -> DepthCase:
    return DepthCase.model_validate(
        {"id": "c", "title": "t", "kind": "instructions", "message": "作って", "expect": expect}
    )


def test_cases_file_has_vague_and_concrete_requests() -> None:
    cases = load_cases(ROOT / DEFAULT_CASES)
    assert len({c.id for c in cases}) == len(cases)
    assert sum(c.expect == "questions" for c in cases) >= 4
    assert sum(c.expect == "proposal" for c in cases) >= 2


async def test_questions_without_proposal_pass_for_vague_requests() -> None:
    llm = ScriptedLlm({"explanation": "確かめます", "questions": QUESTIONS})
    out = await run_case(llm, _case("questions"), "research")
    assert out.passed and out.questions == ["誰が使いますか？"]
    # 新規作成の会話として、調べるターンの規則つきで聞く
    assert "proposal.name" in llm.prompts[0] and "深掘りと調査" in llm.prompts[0]
    plain = ScriptedLlm({"explanation": "確かめます", "questions": QUESTIONS})
    await run_case(plain, _case("questions"), "plain")
    assert "深掘りと調査" not in plain.prompts[0]


async def test_guessing_a_full_draft_fails_for_vague_requests() -> None:
    for reply in (
        {"explanation": "書きました", "questions": QUESTIONS, "proposal": PROPOSAL},
        {"explanation": "書きました", "proposal": PROPOSAL},
        {"explanation": "質問を説明に書いただけ"},
    ):
        out = await run_case(ScriptedLlm(reply), _case("questions"), "plain")
        assert not out.passed


async def test_concrete_requests_need_a_proposal() -> None:
    ok = await run_case(
        ScriptedLlm({"explanation": "書きました", "questions": QUESTIONS, "proposal": PROPOSAL}),
        _case("proposal"),
        "plain",
    )
    assert ok.passed
    only_name = await run_case(
        ScriptedLlm({"explanation": "名前だけ", "proposal": {"name": "dev"}}),
        _case("proposal"),
        "plain",
    )
    assert not only_name.passed
    assert "不合格" in render_markdown("m", [ok, only_name])


async def test_provider_errors_are_retried_then_reported(monkeypatch: Any) -> None:
    monkeypatch.setattr("speclino.eval.chat_depth._RETRY_SECONDS", 0)
    llm = ScriptedLlm(None)
    out = await run_case(llm, _case("questions"), "plain")
    assert out.error == "ValueError" and not out.passed and len(llm.prompts) == 3
