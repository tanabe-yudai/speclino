"""Gemini による構造化出力（TextLlm ポートの実装）。spec-extract が使う。

認証は google-genai の既定（`GOOGLE_API_KEY`、または `GOOGLE_GENAI_USE_VERTEXAI=true` +
ADC）。温度 0 で決定性に寄せるが、LLM 出力は候補にすぎず、承認は人が行う。
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types


class GeminiJsonLlm:
    def __init__(self, model: str, client: genai.Client | None = None) -> None:
        self.model = model
        self._client = client

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client()
        return self._client

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> str:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema,
                temperature=0,
            ),
        )
        return response.text or ""


__all__ = ["GeminiJsonLlm"]
