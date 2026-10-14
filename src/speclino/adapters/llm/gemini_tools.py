"""Gemini による、関数を呼んで調べてから JSON で答える会話（`ToolStreamingLlm` の実装）。

- 関数を選ぶ歩は非ストリームの generate_content。返った `candidates[0].content` を加工せず
  そのまま履歴に戻す（Gemini 3 の thought signature を落とさない）。並列の呼出しは
  まとめて受け、結果（FunctionResponse）もまとめて 1 つの Content で返す。応答の `id` と
  `name` は呼出しのものと同じにする。SDK の自動の関数呼出しは止める
- 最後の返答だけ generate_content_stream で流す。関数の宣言は履歴のために渡すが、
  function_calling_config の mode=NONE で呼ばせない。思考の要約（include_thoughts）も流す。
  最後の返答を求める文（`request`）は、別の利用者の Content として末尾に足す（関数の結果と
  同じ Content に文を混ぜると Vertex AI が拒否する）
- temperature などの生成の設定は送らない（Gemini 3 の推奨は既定値）。思考の量は thinking_level
- 会話の要約など単発の JSON（`TextLlm`）も同じ設定で答える
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from google import genai
from google.genai import types

from speclino.ports.text_llm import (
    JsonGeneration,
    JsonStreamEvent,
    Thought,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStep,
    ToolStreamEvent,
)

DEFAULT_MAX_OUTPUT_TOKENS = 16384
# 関数を選ぶ 1 歩の出力の上限（思考を含む）。選ぶだけなので小さく、超えたら調べ終えたとみなす
DEFAULT_STEP_OUTPUT_TOKENS = 4096


def _usage(usage: Any) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    # 思考トークンも出力として課金されるので出力側に含める
    return (
        usage.prompt_token_count or 0,
        (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
    )


def _parts(response: Any) -> list[types.Part]:
    candidates = getattr(response, "candidates", None) or []
    content = candidates[0].content if candidates else None
    return list(content.parts or []) if content is not None else []


def _truncated(response: Any) -> bool:
    candidates = getattr(response, "candidates", None) or []
    return bool(candidates) and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS


class GeminiToolLlm:
    def __init__(
        self,
        model: str,
        client: genai.Client | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        step_output_tokens: int = DEFAULT_STEP_OUTPUT_TOKENS,
        thinking_level: types.ThinkingLevel = types.ThinkingLevel.MEDIUM,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.step_output_tokens = step_output_tokens
        self.thinking_level = thinking_level
        self._client = client

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client()
        return self._client

    def config(
        self,
        *,
        tools: Sequence[ToolSpec] = (),
        mode: types.FunctionCallingConfigMode | None = None,
        schema: dict[str, Any] | None = None,
        max_output_tokens: int,
    ) -> types.GenerateContentConfig:
        declared = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        return types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=declared)] if declared else None,
            tool_config=(
                types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode))
                if declared and mode is not None
                else None
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json" if schema is not None else None,
            response_json_schema=schema,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(
                thinking_level=self.thinking_level, include_thoughts=True
            ),
        )

    def open(self, prompt: str) -> GeminiToolSession:
        return GeminiToolSession(self, prompt)

    # ---- TextLlm（会話の要約などの単発の JSON）
    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=self.config(schema=schema, max_output_tokens=self.max_output_tokens),
        )
        text = "".join(p.text or "" for p in _parts(response) if not p.thought)
        return JsonGeneration(text, *_usage(response.usage_metadata), _truncated(response))

    async def stream_json(
        self, prompt: str, schema: dict[str, Any]
    ) -> AsyncIterator[JsonStreamEvent]:
        session = self.open(prompt)
        async for event in session.stream_final(schema, ()):
            if not isinstance(event, Thought):
                yield event


class GeminiToolSession:
    """1 回の発言の会話。`_contents` は事業者の Content のまま持ち、加工しない。"""

    def __init__(self, llm: GeminiToolLlm, prompt: str) -> None:
        self._llm = llm
        self._contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]
        self._pending: list[ToolCall] = []

    @property
    def contents(self) -> list[types.Content]:
        return list(self._contents)

    async def step(self, tools: Sequence[ToolSpec]) -> ToolStep:
        if self._pending:
            raise RuntimeError("前の呼出しの結果をまだ返していない")
        response = await self._llm.client.aio.models.generate_content(
            model=self._llm.model,
            contents=list(self._contents),
            config=self._llm.config(
                tools=tools,
                mode=types.FunctionCallingConfigMode.AUTO,
                max_output_tokens=self._llm.step_output_tokens,
            ),
        )
        parts = _parts(response)
        calls = [
            ToolCall(
                name=p.function_call.name or "",
                args=dict(p.function_call.args or {}),
                id=p.function_call.id,
            )
            for p in parts
            if p.function_call is not None
        ]
        thoughts = "".join(p.text or "" for p in parts if p.thought)
        if calls:
            # 呼出しを含む応答は、思考の署名ごとそのまま履歴に戻す
            candidates = response.candidates or []
            content = candidates[0].content
            assert content is not None
            self._contents.append(content)
            self._pending = calls
        return ToolStep(calls, thoughts, *_usage(response.usage_metadata))

    def add_results(self, results: Sequence[ToolResult]) -> None:
        if [r.call for r in results] != self._pending:
            raise ValueError("呼出しと結果が 1 対 1 に揃っていない")
        self._contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=r.call.id, name=r.call.name, response={"output": r.output}
                        )
                    )
                    for r in results
                ],
            )
        )
        self._pending = []

    async def stream_final(
        self, schema: dict[str, Any], tools: Sequence[ToolSpec], request: str | None = None
    ) -> AsyncIterator[ToolStreamEvent]:
        if self._pending:
            raise RuntimeError("前の呼出しの結果をまだ返していない")
        if request is not None:
            self._contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=request)])
            )
        stream = await self._llm.client.aio.models.generate_content_stream(
            model=self._llm.model,
            contents=list(self._contents),
            config=self._llm.config(
                tools=tools,
                mode=types.FunctionCallingConfigMode.NONE,
                schema=schema,
                max_output_tokens=self._llm.max_output_tokens,
            ),
        )
        pieces: list[str] = []
        usage: Any = None
        last: Any = None
        async for chunk in stream:
            for part in _parts(chunk):
                if not part.text:
                    continue
                if part.thought:
                    yield Thought(part.text)
                else:
                    pieces.append(part.text)
                    yield part.text
            if chunk.usage_metadata is not None:
                usage = chunk.usage_metadata
            if getattr(chunk, "candidates", None):
                last = chunk
        yield JsonGeneration("".join(pieces), *_usage(usage), _truncated(last))


__all__ = ["DEFAULT_STEP_OUTPUT_TOKENS", "GeminiToolLlm", "GeminiToolSession"]
