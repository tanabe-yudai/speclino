"""TextLlm ポート: プロンプトから JSON 文字列を生成する（spec-extract 用）。

エージェント（ADK）とは別に、単発の構造化出力だけを要る場面で使う。実装は
adapters/llm/gemini_json.py（Gemini）と adapters/llm/scripted.py（テスト用）。
"""

from __future__ import annotations

from typing import Any, Protocol


class TextLlm(Protocol):
    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> str:
        """schema（JSON Schema）に従う JSON 文字列を返す。"""
        ...


__all__ = ["TextLlm"]
