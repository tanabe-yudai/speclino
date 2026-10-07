"""Gemini による構造化出力（TextLlm ポートの実装）。spec-extract が使う。

認証は google-genai の既定（`GOOGLE_API_KEY`、または `GOOGLE_GENAI_USE_VERTEXAI=true` +
ADC）。温度 0 で決定性に寄せるが、LLM 出力は候補にすぎず、承認は人が行う。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import types

from speclino.ports.text_llm import JsonGeneration, JsonStreamEvent

# 出力の上限。Gemini では思考トークンもこの上限に含まれる。cancel_order 規模の候補 IR
# （要求 9 件）が収まる大きさにしつつ、1 回の呼出しの最悪費用を有限にする
DEFAULT_MAX_OUTPUT_TOKENS = 16384


class GeminiJsonLlm:
    def __init__(
        self,
        model: str,
        client: genai.Client | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        thinking_level: types.ThinkingLevel | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        # 思考の量（Gemini 3 の thinking_level）。None ならモデルの既定。思考トークンも
        # max_output_tokens に含まれるので、会話（S73）は LOW にして出力の枠を本文に残す
        self.thinking_level = thinking_level
        self._client = client

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client()
        return self._client

    def _config(self, schema: dict[str, Any]) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
            max_output_tokens=self.max_output_tokens,
            thinking_config=(
                types.ThinkingConfig(thinking_level=self.thinking_level)
                if self.thinking_level is not None
                else None
            ),
        )

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        response = await self.client.aio.models.generate_content(
            model=self.model, contents=prompt, config=self._config(schema)
        )
        return _generation(response.text or "", response.usage_metadata, response)

    async def stream_json(
        self, prompt: str, schema: dict[str, Any]
    ) -> AsyncIterator[JsonStreamEvent]:
        stream = await self.client.aio.models.generate_content_stream(
            model=self.model, contents=prompt, config=self._config(schema)
        )
        parts: list[str] = []
        usage: Any = None
        last: Any = None
        async for chunk in stream:
            piece = chunk.text or ""
            if piece:
                parts.append(piece)
                yield piece
            if chunk.usage_metadata is not None:
                usage = chunk.usage_metadata
            if getattr(chunk, "candidates", None):
                last = chunk
        yield _generation("".join(parts), usage, last)


def _generation(text: str, usage: Any, response: Any) -> JsonGeneration:
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    # 思考トークンも出力として課金されるので出力側に含める
    output_tokens = (
        (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
    )
    candidates = (getattr(response, "candidates", None) or []) if response is not None else []
    truncated = bool(candidates) and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS
    return JsonGeneration(text, input_tokens, output_tokens, truncated)


__all__ = ["DEFAULT_MAX_OUTPUT_TOKENS", "GeminiJsonLlm"]
