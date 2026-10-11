"""Gemini の Google 検索によるグラウンディングだけを持つ呼出し（`WebSearcher` の実装）。

Vertex AI では Google 検索と関数の呼出しを同じリクエストに入れられないので、会話の
`web_search` 関数の中で、検索だけを持つ別の呼出しをする。答えの要約と、grounding_metadata の
出典（web の title / uri）、検索の候補の表示（search_entry_point.rendered_content）、
問い合わせの件数（web_search_queries。費用の精算に使う）を返す。temperature などは送らない。
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from speclino.ports.text_llm import SearchAnswer, SearchSource

DEFAULT_MAX_OUTPUT_TOKENS = 2048
# 1 回の検索で行われる問い合わせの数の見積もり（予約用）。API で上限を強制できない値なので、
# 多めに見込むだけ。実費は応答の web_search_queries の件数で精算する（見積もりを超えても記録する）
DEFAULT_MAX_QUERIES = 8

_INSTRUCTION = """\
次の問いについて Web を検索し、分かったことを日本語で 800 字以内にまとめる。
推測で補わず、検索で裏が取れたことだけを書く。分からなければ「見つからなかった」と書く。
問い: """


def _usage(usage: Any) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    return (
        usage.prompt_token_count or 0,
        (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
    )


class GeminiSearch:
    def __init__(
        self,
        model: str,
        client: genai.Client | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_queries: int = DEFAULT_MAX_QUERIES,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_queries = max_queries
        self._client = client

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client()
        return self._client

    async def search(self, query: str) -> SearchAnswer:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=_INSTRUCTION + query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                # SDK の既定は自動の関数呼出しが有効で、関数が無くても警告を出す。明示的に止める
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                max_output_tokens=self.max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            ),
        )
        candidates = response.candidates or []
        candidate = candidates[0] if candidates else None
        parts = (candidate.content.parts or []) if candidate and candidate.content else []
        text = "".join(p.text or "" for p in parts if not p.thought)
        meta = candidate.grounding_metadata if candidate else None
        return SearchAnswer(
            text=text.strip(),
            sources=_sources(meta),
            rendered_content=(
                meta.search_entry_point.rendered_content
                if meta and meta.search_entry_point and meta.search_entry_point.rendered_content
                else None
            ),
            query_count=len(meta.web_search_queries or []) if meta else 0,
            input_tokens=_usage(response.usage_metadata)[0],
            output_tokens=_usage(response.usage_metadata)[1],
        )


def _sources(meta: types.GroundingMetadata | None) -> list[SearchSource]:
    out: list[SearchSource] = []
    seen: set[str] = set()
    for chunk in (meta.grounding_chunks or []) if meta else []:
        web = chunk.web
        if web is None or not web.uri or web.uri in seen:
            continue
        seen.add(web.uri)
        out.append(SearchSource(title=web.title or web.domain or web.uri, uri=web.uri))
    return out


__all__ = ["DEFAULT_MAX_QUERIES", "GeminiSearch"]
