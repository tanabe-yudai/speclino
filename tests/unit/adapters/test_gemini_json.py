"""GeminiJsonLlm: 出力トークンの上限を渡し、使用量を返す（費用ガードの最悪費用の前提）。

クライアントは偽物に差し替え、Gemini を呼ばない。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from google.genai import types

from speclino.adapters.llm.gemini_json import GeminiJsonLlm


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            text='{"rules": []}',
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5
            ),
        )


async def test_max_output_tokens_is_passed_and_usage_is_returned() -> None:
    models = _FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    llm = GeminiJsonLlm("gemini-3.8-flash", client=client, max_output_tokens=1234)  # type: ignore[arg-type]
    out = await llm.generate_json("prompt", {"type": "object"})
    [call] = models.calls
    assert call["config"].max_output_tokens == 1234 == llm.max_output_tokens
    assert (out.text, out.input_tokens, out.output_tokens) == ('{"rules": []}', 100, 25)
