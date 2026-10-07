from __future__ import annotations

import asyncio

from speclino.adapters.llm.scripted import ScriptedJsonLlm


def test_scripted_json_llm_returns_responses_in_order() -> None:
    llm = ScriptedJsonLlm(["a", "b"])
    out = [asyncio.run(llm.generate_json("p", {})).text for _ in range(3)]
    assert out == ["a", "b", "b"]


def test_scripted_json_llm_stream_chunks_then_final() -> None:
    async def collect() -> list[object]:
        llm = ScriptedJsonLlm("0123456789", chunk_size=4)
        return [e async for e in llm.stream_json("p", {})]

    out = asyncio.run(collect())
    assert out[:3] == ["0123", "4567", "89"]
    assert getattr(out[3], "text", None) == "0123456789"


def test_scripted_json_llm_single_string_repeats() -> None:
    llm = ScriptedJsonLlm("x")
    assert [asyncio.run(llm.generate_json("p", {})).text for _ in range(2)] == ["x", "x"]
