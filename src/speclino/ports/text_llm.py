"""TextLlm ポート: プロンプトから JSON 文字列を生成する（spec-extract 用）。

エージェント（ADK）とは別に、単発の構造化出力だけを要る場面で使う。実装は
adapters/llm/gemini_json.py（Gemini）と adapters/llm/scripted.py（テスト用）。

`ToolStreamingLlm` は、読むだけの関数を呼んで調べてから最後の JSON を流す（知識の会話）。
実装は adapters/llm/gemini_tools.py。`WebSearcher` は Web 検索だけを持つ別の呼出し
（adapters/llm/gemini_search.py）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class JsonGeneration:
    """1 回の生成結果。トークン数は費用ガード（`services.cost_guard`）の記録に使う。"""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    # 出力の上限で打ち切られた（Gemini の finish_reason == MAX_TOKENS）。text は途中で切れている
    truncated: bool = False


class TextLlm(Protocol):
    # 費用の見積もりに使うモデル名（`cost_guard.DEFAULT_PRICING` のキー）
    model: str
    # 1 回の出力トークンの上限（思考トークンを含む）。費用ガードは最悪費用をこれで見積もる
    max_output_tokens: int

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        """schema（JSON Schema）に従う JSON 文字列と、使ったトークン数を返す。"""
        ...


# stream_json の出力: 文字列の断片（str）を順に流し、最後に全文と使用量（JsonGeneration）を 1 つ返す
JsonStreamEvent = str | JsonGeneration


@runtime_checkable
class StreamingTextLlm(TextLlm, Protocol):
    def stream_json(self, prompt: str, schema: dict[str, Any]) -> AsyncIterator[JsonStreamEvent]:
        """generate_json と同じ内容を、断片に分けて流す。最後の要素は必ず JsonGeneration。

        最後の要素まで読み切ったときだけ使用量が確定する。途中で止めたら（中断・時間切れ）
        使用量は不明で、呼び手は最悪の額を残す。
        """
        ...


# ------------------------------------------------------------------ ツールつき（調べてから答える）
@dataclass(frozen=True)
class ToolSpec:
    """モデルに見せる関数の宣言。`parameters` は引数の JSON Schema（object）。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """モデルが求めた関数の呼出し。`id` は事業者が付けた呼出しの ID（応答で同じ値を返す）。"""

    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """呼出し 1 件の結果。`output` はモデルに返す文字列（区切りに入れた信頼しないデータ）。"""

    call: ToolCall
    output: str


@dataclass(frozen=True)
class ToolStep:
    """関数を選ぶ 1 歩の結果。`calls` が空ならモデルはもう調べない。"""

    calls: list[ToolCall]
    thoughts: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Thought:
    """思考の要約の増えた分（表示用。判断には使わない）。"""

    text: str


# stream_final の出力: 思考の要約（Thought）と JSON の断片（str）を順に流し、最後に JsonGeneration
ToolStreamEvent = str | Thought | JsonGeneration


class ToolSession(Protocol):
    """1 回の発言の中の、関数の呼出しを挟む会話。履歴は実装が持つ（事業者固有の形のまま）。"""

    async def step(self, tools: Sequence[ToolSpec]) -> ToolStep:
        """関数を選ばせる（非ストリーム）。呼出しがあれば、次は `add_results` で全件に答える。"""
        ...

    def add_results(self, results: Sequence[ToolResult]) -> None:
        """直前の `step` の呼出しすべての結果を、まとめて履歴に足す。"""
        ...

    def stream_final(
        self, schema: dict[str, Any], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ToolStreamEvent]:
        """関数を呼ばせずに、schema に従う最後の返答を流す。`tools` は履歴の関数の宣言。"""
        ...


@runtime_checkable
class ToolStreamingLlm(Protocol):
    model: str
    # 最後の返答の出力トークンの上限（思考を含む）
    max_output_tokens: int
    # 関数を選ぶ 1 歩の出力トークンの上限（思考を含む）
    step_output_tokens: int

    def open(self, prompt: str) -> ToolSession: ...


@dataclass(frozen=True)
class SearchSource:
    title: str
    uri: str


@dataclass(frozen=True)
class SearchAnswer:
    """Web 検索の結果。`rendered_content` は Google 検索の候補の表示（表示の要件）。"""

    text: str
    sources: list[SearchSource] = field(default_factory=list)
    rendered_content: str | None = None
    query_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class WebSearcher(Protocol):
    model: str
    max_output_tokens: int
    # 1 回の検索で事業者が行う問い合わせの数の見積もりの上限（費用の予約に使う）
    max_queries: int

    async def search(self, query: str) -> SearchAnswer: ...


__all__ = [
    "JsonGeneration",
    "JsonStreamEvent",
    "SearchAnswer",
    "SearchSource",
    "StreamingTextLlm",
    "TextLlm",
    "Thought",
    "ToolCall",
    "ToolResult",
    "ToolSession",
    "ToolSpec",
    "ToolStep",
    "ToolStreamEvent",
    "ToolStreamingLlm",
    "WebSearcher",
]
