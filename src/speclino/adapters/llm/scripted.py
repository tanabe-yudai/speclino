"""テスト用 LLM。台本どおりの応答（関数呼出し／テキスト）を順に返す。決定的。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any, ClassVar

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from speclino.ports.text_llm import (
    JsonGeneration,
    JsonStreamEvent,
    SearchAnswer,
    Thought,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStep,
    ToolStreamEvent,
)


def call(name: str, **args: Any) -> types.Part:
    return types.Part.from_function_call(name=name, args=args)


def text(s: str) -> types.Part:
    return types.Part.from_text(text=s)


class ScriptedLlm(BaseLlm):
    """`steps` の各要素が 1 回の応答（parts）。尽きたら最後の要素を繰り返す。"""

    model: str = "scripted"
    steps: list[list[types.Part]] = []
    requests: ClassVar[list[LlmRequest]] = []
    usage_per_call: tuple[int, int] = (1000, 200)

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["scripted"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        type(self).requests.append(llm_request)
        idx = min(len(type(self).requests) - 1, len(self.steps) - 1)
        parts = self.steps[idx] if self.steps else [text("done")]
        yield LlmResponse(
            content=types.Content(role="model", parts=list(parts)),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=self.usage_per_call[0],
                candidates_token_count=self.usage_per_call[1],
                total_token_count=sum(self.usage_per_call),
            ),
            turn_complete=True,
        )

    @classmethod
    def reset(cls) -> None:
        cls.requests = []


class ScriptedJsonLlm:
    """TextLlm ポートのテスト用実装。与えた JSON 文字列をそのまま返し、プロンプトを記録する。

    列を渡すと呼出しごとに順に返し、尽きたら最後を返す。
    """

    model = "scripted"
    max_output_tokens = 8192

    def __init__(
        self,
        response: str | list[str],
        usage: tuple[int, int] = (1000, 200),
        chunk_size: int = 8,
    ) -> None:
        self.chunk_size = chunk_size
        self.responses = [response] if isinstance(response, str) else list(response)
        self.usage = usage
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        idx = min(len(self.prompts) - 1, len(self.responses) - 1)
        return JsonGeneration(self.responses[idx], *self.usage)

    async def stream_json(
        self, prompt: str, schema: dict[str, Any]
    ) -> AsyncIterator[JsonStreamEvent]:
        """generate_json の応答を `chunk_size` 文字ずつに分けて流し、最後に全文と使用量を返す。"""
        gen = await self.generate_json(prompt, schema)
        step = self.chunk_size
        for i in range(0, len(gen.text), step):
            yield gen.text[i : i + step]
        yield gen


class ScriptedToolLlm:
    """ToolStreamingLlm のテスト用実装。`steps` の各要素が 1 歩で求める呼出し（尽きたら空）。

    各歩に渡された関数の名前（`declared`）と、返された結果（`results`）を記録する。最後の
    返答は `final` を `chunk_size` 文字ずつ流す（思考の要約 `thought` を先に流す）。
    """

    model = "scripted"
    max_output_tokens = 8192
    step_output_tokens = 1024

    def __init__(
        self,
        steps: list[list[ToolCall]],
        final: str,
        *,
        thought: str = "考えています",
        usage: tuple[int, int] = (1000, 200),
        chunk_size: int = 8,
    ) -> None:
        self.steps = steps
        self.final = final
        self.thought = thought
        self.usage = usage
        self.chunk_size = chunk_size
        self.prompts: list[str] = []
        self.declared: list[list[str]] = []
        self.results: list[list[ToolResult]] = []
        self.final_declared: list[str] | None = None
        self.step_hook: Any = None

    def open(self, prompt: str) -> _ScriptedToolSession:
        self.prompts.append(prompt)
        return _ScriptedToolSession(self)


class _ScriptedToolSession:
    def __init__(self, llm: ScriptedToolLlm) -> None:
        self.llm = llm
        self.pending: list[ToolCall] = []

    async def step(self, tools: Sequence[ToolSpec]) -> ToolStep:
        assert not self.pending, "前の呼出しの結果を返していない"
        llm = self.llm
        idx = len(llm.declared)
        llm.declared.append([t.name for t in tools])
        if llm.step_hook is not None:
            await llm.step_hook(idx)
        calls = llm.steps[idx] if idx < len(llm.steps) else []
        self.pending = list(calls)
        return ToolStep(list(calls), f"{llm.thought}（{idx + 1}）", *llm.usage)

    def add_results(self, results: Sequence[ToolResult]) -> None:
        assert [r.call for r in results] == self.pending, "呼出しと結果が揃っていない"
        self.llm.results.append(list(results))
        self.pending = []

    async def stream_final(
        self, schema: dict[str, Any], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ToolStreamEvent]:
        assert not self.pending
        llm = self.llm
        llm.final_declared = [t.name for t in tools]
        yield Thought(llm.thought)
        for i in range(0, len(llm.final), llm.chunk_size):
            yield llm.final[i : i + llm.chunk_size]
        yield JsonGeneration(llm.final, *llm.usage)


class ScriptedSearch:
    """WebSearcher のテスト用実装。問いを記録し、決まった答えを返す。"""

    model = "scripted"
    max_output_tokens = 512
    max_queries = 2

    def __init__(self, answer: SearchAnswer) -> None:
        self.answer = answer
        self.queries: list[str] = []

    async def search(self, query: str) -> SearchAnswer:
        self.queries.append(query)
        return self.answer
