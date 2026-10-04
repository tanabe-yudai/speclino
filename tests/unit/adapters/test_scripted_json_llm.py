from __future__ import annotations

import asyncio

from speclino.adapters.llm.scripted import ScriptedJsonLlm


def test_scripted_json_llm_returns_responses_in_order() -> None:
    llm = ScriptedJsonLlm(["a", "b"])
    out = [asyncio.run(llm.generate_json("p", {})).text for _ in range(3)]
    assert out == ["a", "b", "b"]


def test_scripted_json_llm_single_string_repeats() -> None:
    llm = ScriptedJsonLlm("x")
    assert [asyncio.run(llm.generate_json("p", {})).text for _ in range(2)] == ["x", "x"]
