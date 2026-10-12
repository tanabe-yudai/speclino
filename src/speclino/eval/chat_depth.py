"""知識のチャットの深掘りを実 Gemini で測る（`make eval-chat-depth`。CI では回さない）。

`eval/chat_depth/cases.yaml` の各ケースを、新規作成の会話の最初の発言として、本番と同じ
プロンプト（`build_chat_prompt`、名前の案あり）・応答の JSON Schema・会話の LLM の設定
（`GeminiToolLlm`、`chat_thinking_level`）で 1 回ずつ呼び、返答を決定的に判定する:

- `expect: questions` … サーバの検証を通った質問（`extract_questions`）が 1 問以上あり、提案
  （`extract_proposal`）が無い
- `expect: proposal` … 提案がある

各ケースは 2 通りのプロンプトで回す: 調べるターン（ストリームの発言。深掘りと調査の規則つき）
と、調べないターン（非ストリームの発言）。調べるターンは関数を呼ばずに最後の返答を求めた場合と
同じ入力になる。認証はアプリと同じ（GOOGLE_GENAI_USE_VERTEXAI ＋ ADC、または GEMINI_API_KEY）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from google.genai import types
from pydantic import BaseModel

from speclino.adapters.llm.gemini_tools import GeminiToolLlm
from speclino.domain.knowledge_chat import KnowledgeChatKind
from speclino.ports.text_llm import TextLlm
from speclino.services.knowledge_chat import CHAT_OUTPUT_TOKENS
from speclino.services.knowledge_chat_prompt import build_chat_prompt
from speclino.services.knowledge_chat_proposal import (
    extract_proposal,
    extract_questions,
    response_schema,
)
from speclino.settings import Settings

DEFAULT_CASES = Path("eval/chat_depth/cases.yaml")
_ATTEMPTS = 3
_RETRY_SECONDS = 10
_THINKING_LEVELS = {
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}
Expect = Literal["questions", "proposal"]
Mode = Literal["research", "plain"]
MODES: tuple[Mode, ...] = ("research", "plain")
_MODE_LABELS: dict[Mode, str] = {"research": "調べる", "plain": "調べない"}


class DepthCase(BaseModel):
    id: str
    title: str
    kind: KnowledgeChatKind
    message: str
    expect: Expect


class DepthOutcome(BaseModel):
    id: str
    title: str
    mode: Mode
    expect: Expect
    questions: list[str] = []
    proposed: bool = False
    error: str | None = None
    explanation: str = ""

    @property
    def passed(self) -> bool:
        if self.error is not None:
            return False
        if self.expect == "questions":
            return bool(self.questions) and not self.proposed
        return self.proposed


def load_cases(path: Path) -> list[DepthCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [DepthCase.model_validate(c) for c in data["cases"]]


async def run_case(llm: TextLlm, case: DepthCase, mode: Mode) -> DepthOutcome:
    out = DepthOutcome(id=case.id, title=case.title, mode=mode, expect=case.expect)
    prompt = build_chat_prompt(
        case.kind,
        message=case.message,
        editor_text="",
        names=[],
        history=[],
        propose_name=case.kind != "policy",
        research_repos=[] if mode == "research" else None,
    )
    schema = response_schema(case.kind, propose_name=True)
    for attempt in range(_ATTEMPTS):
        try:
            gen = await llm.generate_json(prompt, schema)
            data: Any = json.loads(gen.text)
            break
        except Exception as e:  # noqa: BLE001 - 1 件の失敗で評価全体を止めない
            # 一時的な失敗（429 など）はやり直す。判定の不合格はやり直さない
            if attempt + 1 < _ATTEMPTS:
                await asyncio.sleep(_RETRY_SECONDS * (attempt + 1))
                continue
            out.error = type(e).__name__
            return out
    if not isinstance(data, dict) or not isinstance(data.get("explanation"), str):
        out.error = "explanation が無い"
        return out
    out.explanation = data["explanation"]
    out.questions = [q.question for q in extract_questions(data.get("questions"))]
    raw, _ = extract_proposal(case.kind, data.get("proposal"), propose_name=True)
    out.proposed = raw is not None
    return out


def _cell(text: str, limit: int = 80) -> str:
    one = " ".join(text.split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def render_markdown(model: str, outcomes: list[DepthOutcome]) -> str:
    passed = sum(1 for o in outcomes if o.passed)
    lines = [
        "# 知識のチャットの深掘りの評価",
        "",
        f"モデル: `{model}`。合格 {passed} / {len(outcomes)}。",
        "",
        "| ケース | 内容 | ターン | 期待 | 質問 | 提案 | 結果 | 最初の質問 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for o in outcomes:
        expect = "質問" if o.expect == "questions" else "提案"
        result = "合格" if o.passed else f"不合格{f'（{o.error}）' if o.error else ''}"
        first = _cell(o.questions[0]) if o.questions else ""
        lines.append(
            f"| {o.id} | {o.title} | {_MODE_LABELS[o.mode]} | {expect} | {len(o.questions)} 問 | "
            f"{'あり' if o.proposed else 'なし'} | {result} | {first} |"
        )
    return "\n".join(lines) + "\n"


async def run_all(llm: TextLlm, cases: list[DepthCase]) -> list[DepthOutcome]:
    return [await run_case(llm, c, mode) for c in cases for mode in MODES]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="知識のチャットの深掘りを実 Gemini で評価する（費用がかかる）"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--model", default=None, help="既定は設定の chat_model（会話と同じ）")
    args = parser.parse_args(argv)
    settings = Settings()
    model = args.model or settings.chat_model or settings.a2a_model or settings.agent_model
    llm = GeminiToolLlm(
        model,
        max_output_tokens=CHAT_OUTPUT_TOKENS,
        thinking_level=_THINKING_LEVELS[settings.chat_thinking_level],
    )
    outcomes = asyncio.run(run_all(llm, load_cases(args.cases)))
    md = render_markdown(model, outcomes)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0 if all(o.passed for o in outcomes) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
