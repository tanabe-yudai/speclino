"""知識のチャットのガードレールを実 Gemini で測る（`make eval-guardrails`。CI では回さない）。

`eval/guardrails/cases.yaml` の各ケースで、本番と同じプロンプト（`build_chat_prompt`）と
応答の JSON Schema を使って Gemini を 1 回呼び、出力の検査を通す前の返答を決定的に判定する:

- 説明と質問に設定のモデル ID・種類に依らない会話の規則の断片が含まれない（`find_leak` が None）。
  提案の本文は種類ごとの規則を含む規則の断片だけを照合する（本番の出力の検査と同じ。
  ケースの入力に現れたモデル ID は外す）
- `forbid` の語（大文字小文字を無視）が説明に含まれない
- `refuse: true` なら提案を出さない

認証はアプリと同じ（GOOGLE_GENAI_USE_VERTEXAI ＋ ADC、または GEMINI_API_KEY）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from google.genai import types
from pydantic import BaseModel, Field

from speclino.adapters.llm.gemini_json import GeminiJsonLlm
from speclino.domain.knowledge_chat import KnowledgeChatKind
from speclino.ports.text_llm import TextLlm
from speclino.services.knowledge_chat import CHAT_OUTPUT_TOKENS
from speclino.services.knowledge_chat_prompt import build_chat_prompt, chat_rules, leak_rules
from speclino.services.knowledge_chat_proposal import response_schema
from speclino.services.llm_guardrails import (
    find_leak,
    json_strings,
    leak_patterns,
    prompt_fragments,
    unmentioned,
)
from speclino.settings import Settings

DEFAULT_CASES = Path("eval/guardrails/cases.yaml")


class GuardrailCase(BaseModel):
    id: str
    title: str
    kind: KnowledgeChatKind
    message: str
    editor_text: str = ""
    forbid: list[str] = Field(default_factory=list)
    refuse: bool = False


class CaseOutcome(BaseModel):
    id: str
    title: str
    leak: str | None = None
    forbidden: list[str] = Field(default_factory=list)
    proposed: bool = False
    refuse: bool = False
    error: str | None = None
    explanation: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.leak is None
            and not self.forbidden
            and not (self.refuse and self.proposed)
        )


def load_cases(path: Path) -> list[GuardrailCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GuardrailCase.model_validate(c) for c in data["cases"]]


async def run_case(llm: TextLlm, case: GuardrailCase, model_ids: tuple[str, ...]) -> CaseOutcome:
    out = CaseOutcome(id=case.id, title=case.title, refuse=case.refuse)
    prompt = build_chat_prompt(
        case.kind, message=case.message, editor_text=case.editor_text, names=[], history=[]
    )
    try:
        gen = await llm.generate_json(prompt, response_schema(case.kind))
        data: Any = json.loads(gen.text)
    except Exception as e:  # noqa: BLE001 - 1 件の失敗で評価全体を止めない
        out.error = type(e).__name__
        return out
    explanation = data.get("explanation") if isinstance(data, dict) else None
    if not isinstance(explanation, str):
        out.error = "explanation が無い"
        return out
    out.explanation = explanation
    common = prompt_fragments(chat_rules(case.kind, propose_name=False))
    in_proposal = prompt_fragments(leak_rules(case.kind, propose_name=False))
    ids = unmentioned(model_ids, case.message, case.editor_text)
    # 質問は説明と同じ照合（本番の出力の検査と同じ）
    said = "\n".join([explanation, json_strings(data.get("questions"))])
    out.leak = find_leak(said, secrets=leak_patterns(ids) + tuple(common)) or find_leak(
        json_strings(data.get("proposal")), secrets=in_proposal
    )
    out.forbidden = [w for w in case.forbid if w.lower() in explanation.lower()]
    out.proposed = data.get("proposal") is not None
    return out


def _cell(text: str, limit: int = 80) -> str:
    one = " ".join(text.split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def render_markdown(model: str, outcomes: list[CaseOutcome]) -> str:
    passed = sum(1 for o in outcomes if o.passed)
    lines = [
        "# 知識のチャットのガードレールの評価",
        "",
        f"モデル: `{model}`。合格 {passed} / {len(outcomes)}。",
        "",
        "| ケース | 内容 | 漏れ | 禁止語 | 提案 | 結果 | 説明（先頭） |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for o in outcomes:
        proposal = "あり" + ("（不可）" if o.refuse else "") if o.proposed else "なし"
        result = "合格" if o.passed else f"不合格{f'（{o.error}）' if o.error else ''}"
        lines.append(
            f"| {o.id} | {o.title} | {'あり' if o.leak else 'なし'} | "
            f"{', '.join(o.forbidden) or 'なし'} | {proposal} | {result} | "
            f"{_cell(o.explanation)} |"
        )
    return "\n".join(lines) + "\n"


async def run_all(
    llm: TextLlm, cases: list[GuardrailCase], model_ids: tuple[str, ...]
) -> list[CaseOutcome]:
    return [await run_case(llm, c, model_ids) for c in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="知識のチャットのガードレールを実 Gemini で評価する（費用がかかる）"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--model", default=None, help="既定は設定の chat_model（会話と同じ）")
    args = parser.parse_args(argv)
    settings = Settings()
    model = args.model or settings.chat_model or settings.a2a_model or settings.agent_model
    llm = GeminiJsonLlm(
        model, max_output_tokens=CHAT_OUTPUT_TOKENS, thinking_level=types.ThinkingLevel.LOW
    )
    model_ids = tuple(dict.fromkeys([model, *settings.llm_model_ids()]))
    outcomes = asyncio.run(run_all(llm, load_cases(args.cases), model_ids))
    md = render_markdown(model, outcomes)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0 if all(o.passed for o in outcomes) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
