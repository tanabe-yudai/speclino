"""GeminiJsonLlm.stream_json（S74）。google-genai のクライアントを偽物に差し替える。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from google.genai import types

from speclino.adapters.llm.gemini_json import GeminiJsonLlm
from speclino.ports.text_llm import JsonGeneration


def _chunk(text: str, usage: Any = None, finish: Any = None) -> Any:
    candidates = [SimpleNamespace(finish_reason=finish)] if finish is not None else None
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=candidates)


class _Models:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.config: Any = None

    async def generate_content_stream(self, **kw: Any) -> AsyncIterator[Any]:
        self.config = kw["config"]

        async def gen() -> AsyncIterator[Any]:
            for c in self.chunks:
                yield c

        return gen()


def _llm(chunks: list[Any]) -> tuple[GeminiJsonLlm, _Models]:
    models = _Models(chunks)
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return GeminiJsonLlm("m", client=client, max_output_tokens=100), models  # type: ignore[arg-type]


def _collect(llm: GeminiJsonLlm) -> list[Any]:
    async def go() -> list[Any]:
        return [e async for e in llm.stream_json("p", {"type": "object"})]

    return asyncio.run(go())


def test_stream_yields_text_then_generation_with_usage() -> None:
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=3)
    llm, models = _llm([_chunk("ab"), _chunk("cd", usage, types.FinishReason.STOP)])
    out = _collect(llm)
    assert out[:2] == ["ab", "cd"]
    final = out[2]
    assert isinstance(final, JsonGeneration)
    assert (final.text, final.input_tokens, final.output_tokens, final.truncated) == (
        "abcd",
        10,
        8,
        False,
    )
    assert models.config.response_mime_type == "application/json"
    assert models.config.max_output_tokens == 100


def test_stream_marks_max_tokens_as_truncated() -> None:
    llm, _ = _llm([_chunk("{", None, types.FinishReason.MAX_TOKENS)])
    final = _collect(llm)[-1]
    assert isinstance(final, JsonGeneration) and final.truncated
