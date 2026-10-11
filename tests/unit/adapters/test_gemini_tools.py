"""GeminiToolLlm / GeminiSearch: 関数の呼出しの履歴の扱いと、検索の出典の取り出し。

クライアントは偽物に差し替え、Gemini を呼ばない。確かめること:
- 呼出しを含むモデルの Content を加工せずに履歴へ戻す（thought signature を落とさない）
- 並列の呼出しの結果は 1 つの Content にまとめ、id と name を呼出しと揃える
- 最後の返答は mode=NONE で関数を呼ばせず、思考の要約と本文を分けて流す
- temperature を送らず、自動の関数呼出しを止める
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from speclino.adapters.llm.gemini_search import GeminiSearch
from speclino.adapters.llm.gemini_tools import GeminiToolLlm
from speclino.ports.text_llm import JsonGeneration, Thought, ToolResult, ToolSpec

SPEC = ToolSpec("repo_read", "読む", {"type": "object", "properties": {}})
USAGE = types.GenerateContentResponseUsageMetadata(
    prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5
)


def _response(parts: list[types.Part], finish: types.FinishReason = types.FinishReason.STOP) -> Any:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)
        ],
        usage_metadata=USAGE,
    )


class _Models:
    def __init__(self, responses: list[Any], chunks: list[Any] | None = None) -> None:
        self.responses = responses
        self.chunks = chunks or []
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append({**kwargs, "contents": list(kwargs["contents"])})
        return self.responses.pop(0)

    async def generate_content_stream(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.stream_calls.append({**kwargs, "contents": list(kwargs["contents"])})

        async def gen() -> AsyncIterator[Any]:
            for c in self.chunks:
                yield c

        return gen()


def _llm(models: _Models) -> GeminiToolLlm:
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return GeminiToolLlm("gemini-3.8-flash", client=client)  # type: ignore[arg-type]


async def test_model_content_is_kept_and_results_match_ids() -> None:
    signed = types.Part(
        function_call=types.FunctionCall(id="c1", name="repo_read", args={"path": "a.py"}),
        thought_signature=b"sig",
    )
    second = types.Part(function_call=types.FunctionCall(id="c2", name="knowledge_list", args={}))
    thought = types.Part(text="コードを見る", thought=True)
    response = _response([thought, signed, second])
    models = _Models([response])
    session = _llm(models).open("依頼")
    step = await session.step([SPEC])
    assert [c.id for c in step.calls] == ["c1", "c2"]
    assert step.thoughts == "コードを見る"
    assert (step.input_tokens, step.output_tokens) == (100, 25)

    config = models.calls[0]["config"]
    assert config.temperature is None
    assert config.automatic_function_calling.disable is True
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.AUTO
    assert config.thinking_config.include_thoughts is True
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM

    with pytest.raises(RuntimeError):  # 結果を返す前に次の歩へ進まない
        await session.step([SPEC])
    with pytest.raises(ValueError):  # 一部だけ返すのは不可
        session.add_results([ToolResult(step.calls[0], "x")])
    session.add_results([ToolResult(step.calls[0], "本文"), ToolResult(step.calls[1], "一覧")])

    # モデルの Content は受け取ったものをそのまま（署名つきの Part を含む）
    assert session.contents[1] is response.candidates[0].content
    assert session.contents[1].parts[1].thought_signature == b"sig"
    reply = session.contents[2]
    assert reply.role == "user"
    responses = [p.function_response for p in reply.parts or []]
    assert [(r.id, r.name, r.response) for r in responses if r] == [
        ("c1", "repo_read", {"output": "本文"}),
        ("c2", "knowledge_list", {"output": "一覧"}),
    ]


async def test_step_without_calls_is_not_added_to_history() -> None:
    models = _Models([_response([types.Part(text="準備完了")])])
    session = _llm(models).open("依頼")
    step = await session.step([SPEC])
    assert step.calls == []
    assert len(session.contents) == 1


async def test_final_stream_forbids_calls_and_separates_thoughts() -> None:
    chunks = [
        _response([types.Part(text="考え中", thought=True)]),
        _response([types.Part(text='{"explanation":')]),
        _response([types.Part(text='"ok"}')], types.FinishReason.STOP),
    ]
    models = _Models([], chunks)
    session = _llm(models).open("依頼")
    events = [e async for e in session.stream_final({"type": "object"}, [SPEC])]
    assert events[0] == Thought("考え中")
    assert events[1:3] == ['{"explanation":', '"ok"}']
    final = events[-1]
    assert isinstance(final, JsonGeneration)
    assert final.text == '{"explanation":"ok"}' and not final.truncated
    config = models.stream_calls[0]["config"]
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE
    assert config.response_json_schema == {"type": "object"}
    assert config.temperature is None


async def test_search_returns_sources_entry_point_and_query_count() -> None:
    meta = types.GroundingMetadata(
        grounding_chunks=[
            types.GroundingChunk(
                web=types.GroundingChunkWeb(title="Doc", uri="https://a.example/x")
            ),
            types.GroundingChunk(
                web=types.GroundingChunkWeb(title="Dup", uri="https://a.example/x")
            ),
        ],
        search_entry_point=types.SearchEntryPoint(rendered_content="<div>chips</div>"),
        web_search_queries=["q1", "q2"],
    )
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="要点")]),
                grounding_metadata=meta,
            )
        ],
        usage_metadata=USAGE,
    )
    models = _Models([response])
    search = GeminiSearch(
        "gemini-3.8-flash", client=SimpleNamespace(aio=SimpleNamespace(models=models))
    )  # type: ignore[arg-type]
    answer = await search.search("pytest の fixture")
    assert answer.text == "要点"
    assert [(s.title, s.uri) for s in answer.sources] == [("Doc", "https://a.example/x")]
    assert answer.rendered_content == "<div>chips</div>"
    assert answer.query_count == 2
    config = models.calls[0]["config"]
    assert config.tools[0].google_search is not None
    assert config.tools[0].function_declarations is None
    assert config.temperature is None
