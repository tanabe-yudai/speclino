"""会話（S73）の台本の LLM。開発と E2E 用（`SPECLINO_CHAT_LLM=scripted`、env=prod では使えない）。

ネットワークを使わず、応答の JSON Schema から種類を見分けて決まった応答を返す。
要約の依頼（what / why / dropped）には決まった要約を返す。費用は 0（モデル名 `scripted`）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from speclino.ports.text_llm import JsonGeneration, JsonStreamEvent

_PROPOSALS: dict[str, dict[str, Any]] = {
    "markdown": {
        "markdown": "# 注文の取消\n\n注文は支払い前なら取り消せる。\n",
    },
    "skill_md": {
        "skill_md": (
            "---\nname: cli-usage\ndescription: 社内 CLI の使い方\n---\n\n"
            "# 社内 CLI の使い方\n\n- `foo --help` で使い方を確かめる\n"
        ),
        "files": [],
    },
    "body": {"body": "# 開発の指示\n\n- 変更の前にテストを書く\n- `make check` を通す\n"},
    "rule": {
        "rule": {
            "id": "DOC-001",
            "title": "README を置く",
            "text": "各リポジトリに README.md を置く",
            "level": "advisory",
            "tier": "deterministic",
        }
    },
}


class ScriptedChatLlm:
    model = "scripted"
    max_output_tokens = 4096

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        props = schema.get("properties", {})
        if "what" in props:
            body: dict[str, Any] = {
                "what": "下書きを AI と作った",
                "why": "会話で決めた内容を残すため",
                "dropped": "なし",
            }
        else:
            fields = props.get("proposal", {}).get("properties", {})
            key = next((k for k in _PROPOSALS if k in fields), "body")
            proposal = dict(_PROPOSALS[key])
            if "name" in fields:  # 新規作成の会話では名前の案も返す（S79）
                proposal["name"] = "scripted-draft"
            body = {
                "explanation": "下書きの案を作りました（台本の応答）",
                "proposal": proposal,
            }
        return JsonGeneration(json.dumps(body, ensure_ascii=False), 0, 0)

    async def stream_json(
        self, prompt: str, schema: dict[str, Any]
    ) -> AsyncIterator[JsonStreamEvent]:
        gen = await self.generate_json(prompt, schema)
        size = max(
            1, min(len(gen.text) // 5, 8)
        )  # 提案の本文が途中経過として何度も届くように細かく切る
        for i in range(0, len(gen.text), size):
            await asyncio.sleep(0.1)
            yield gen.text[i : i + size]
        yield gen


__all__ = ["ScriptedChatLlm"]
