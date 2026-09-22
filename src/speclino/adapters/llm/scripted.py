"""テスト用 LLM。台本どおりの応答（関数呼出し／テキスト）を順に返す。決定的。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


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
