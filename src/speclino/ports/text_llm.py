"""TextLlm ポート: プロンプトから JSON 文字列を生成する（spec-extract 用）。

エージェント（ADK）とは別に、単発の構造化出力だけを要る場面で使う。実装は
adapters/llm/gemini_json.py（Gemini）と adapters/llm/scripted.py（テスト用）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
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


__all__ = ["JsonGeneration", "JsonStreamEvent", "StreamingTextLlm", "TextLlm"]
