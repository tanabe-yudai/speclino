"""TextLlm ポート: プロンプトから JSON 文字列を生成する（spec-extract 用）。

エージェント（ADK）とは別に、単発の構造化出力だけを要る場面で使う。実装は
adapters/llm/gemini_json.py（Gemini）と adapters/llm/scripted.py（テスト用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class JsonGeneration:
    """1 回の生成結果。トークン数は費用ガード（`services.cost_guard`）の記録に使う。"""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class TextLlm(Protocol):
    # 費用の見積もりに使うモデル名（`cost_guard.DEFAULT_PRICING` のキー）
    model: str
    # 1 回の出力トークンの上限（思考トークンを含む）。費用ガードは最悪費用をこれで見積もる
    max_output_tokens: int

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        """schema（JSON Schema）に従う JSON 文字列と、使ったトークン数を返す。"""
        ...


__all__ = ["JsonGeneration", "TextLlm"]
