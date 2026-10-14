"""知識の会話の「調べてから答える」1 ターン（関数の呼出しのループ、上限、費用、出典）。

- 関数を選ぶ歩（非ストリーム）→ 結果をまとめて返す、を繰り返し、最後の JSON の返答だけ流す
- 上限: 1 ターンの呼出し `max_calls` 回・選ぶ歩 `max_steps` 回・調べる時間 `seconds` 秒・
  結果の合計 `results_max_bytes`・検索 `searches_max` 回。超えたら関数なしで最後の返答を求める
  （上限を超えた呼出しにも、断った旨の結果を返す。呼出しと結果は 1 対 1 に揃える）
- `web_search` は、そのターンで知識の本文・コード（`PRIVATE_READS`）を読んだ後は宣言から外し、
  呼ばれても断る（非公開の中身を検索の問いに載せて外へ出さない）
- 費用: ターンの予算（`turn_budget`）を最初に 1 回だけ予約する。予算は「最後の返答の最悪の額」
  ＋「調べる分の枠」で、調べる歩・検索は、その最悪の額が枠に収まるときだけ行う。だから実費は
  予算を超えない。終わりに実費（トークンと、検索の問い合わせの件数）で精算する
- 出典は、成功した関数の実行と検索のグラウンディングから、サーバが組み立てる（AI に書かせない）
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from speclino.domain.knowledge_chat import (
    CHAT_SOURCES_MAX,
    SEARCH_SUGGESTION_MAX_BYTES,
    SEARCH_SUGGESTIONS_MAX,
    ChatSource,
)
from speclino.ports.repo_reader import RepoReader
from speclino.ports.text_llm import (
    JsonGeneration,
    Thought,
    ToolCall,
    ToolResult,
    ToolSession,
    ToolSpec,
    ToolStreamingLlm,
    WebSearcher,
)
from speclino.services.cost_guard import CostGuard
from speclino.services.knowledge_chat_prompt import FINAL_REQUEST
from speclino.services.knowledge_chat_tools import PRIVATE_READS, ChatTools, ToolOutcome

logger = logging.getLogger(__name__)

# 履歴の大きさの見積もりに足す分（選ぶ歩ごとの思考の署名・呼出しの形、呼出しごとの引数）
_STEP_OVERHEAD_BYTES = 4 * 1024
_CALL_OVERHEAD_BYTES = 1024
# 検索の呼出しの入力の見積もり（指示と問い）
_SEARCH_INPUT_BYTES = 4 * 1024
_LIMIT_REACHED = "エラー: このターンで調べられる量の上限に達しました。今ある情報で答えてください"
_SEARCH_CLOSED = "エラー: 非公開の情報を読んだ後は Web を検索できません"
_TIMED_OUT = "エラー: 時間切れで読めませんでした"


@dataclass(frozen=True)
class ResearchLimits:
    max_calls: int = 8
    max_steps: int = 4
    seconds: float = 180.0
    results_max_bytes: int = 64 * 1024
    searches_max: int = 2
    step_timeout: float = 60.0
    tool_timeout: float = 30.0
    # 予算に見込む「調べる歩」の数（平均的な大きさの履歴で）。実際の歩の数はこの予算の中で決まる
    budget_steps: int = 2
    # 予算に見込む検索の回数
    budget_searches: int = 1


@dataclass(frozen=True)
class ChatResearch:
    """会話の調査の配線。リポジトリ・検索が無ければ、その関数を出さない。"""

    reader: RepoReader | None = None
    searcher: WebSearcher | None = None
    limits: ResearchLimits = field(default_factory=ResearchLimits)


@dataclass(frozen=True)
class ToolActivity:
    """今調べていること（人向けの文）。"""

    tool: str
    text: str


@dataclass(frozen=True)
class ResearchDone:
    """ターンの結果。`gen` のトークンは LLM の全歩の合計、`extra_usd` は検索の費用と、
    使用量の分からない歩の最悪の額。"""

    gen: JsonGeneration
    extra_usd: Decimal
    sources: list[ChatSource]
    search_suggestions: list[str]
    tool_counts: dict[str, int]


ResearchEvent = str | Thought | ToolActivity | ResearchDone


def _final_input_bytes(prompt_bytes: int, limits: ResearchLimits) -> int:
    """最後の返答の入力の上界（バイト数をトークン数の上界とみなす）。"""
    return (
        prompt_bytes
        + limits.results_max_bytes
        + limits.max_steps * _STEP_OVERHEAD_BYTES
        + limits.max_calls * _CALL_OVERHEAD_BYTES
    )


def _search_worst(guard: CostGuard, searcher: WebSearcher) -> Decimal:
    return guard.estimate(
        searcher.model, _SEARCH_INPUT_BYTES, searcher.max_output_tokens
    ) + guard.search_cost(searcher.max_queries)


def final_worst(
    guard: CostGuard, llm: ToolStreamingLlm, prompt_bytes: int, limits: ResearchLimits
) -> Decimal:
    return guard.estimate(
        llm.model, _final_input_bytes(prompt_bytes, limits), llm.max_output_tokens
    )


def turn_budget(
    guard: CostGuard,
    llm: ToolStreamingLlm,
    searcher: WebSearcher | None,
    prompt_bytes: int,
    limits: ResearchLimits,
) -> Decimal:
    """1 ターンで予約する額。最後の返答の最悪の額＋調べる分の枠（`research_allowance`）。"""
    return final_worst(guard, llm, prompt_bytes, limits) + research_allowance(
        guard, llm, searcher, prompt_bytes, limits
    )


def research_allowance(
    guard: CostGuard,
    llm: ToolStreamingLlm,
    searcher: WebSearcher | None,
    prompt_bytes: int,
    limits: ResearchLimits,
) -> Decimal:
    average = prompt_bytes + limits.results_max_bytes // 2 + limits.max_steps * _STEP_OVERHEAD_BYTES
    steps = guard.estimate(
        llm.model,
        limits.budget_steps * average,
        limits.budget_steps * llm.step_output_tokens,
    )
    searches = (
        _search_worst(guard, searcher) * limits.budget_searches if searcher is not None else 0
    )
    return steps + Decimal(searches)


@dataclass
class _Turn:
    """1 ターンの勘定（費用・量・出典）。"""

    allowance: Decimal
    history_bytes: int
    spent_usd: Decimal = Decimal("0")
    extra_usd: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0
    result_bytes: int = 0
    calls: int = 0
    searches: int = 0
    private_seen: bool = False
    sources: dict[str, ChatSource] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    declared: dict[str, ToolSpec] = field(default_factory=dict)

    def fits(self, worst: Decimal) -> bool:
        return self.spent_usd + worst <= self.allowance

    def add_sources(self, sources: list[ChatSource]) -> None:
        for s in sources:
            if len(self.sources) >= CHAT_SOURCES_MAX:
                return
            self.sources.setdefault(s.ref, s)


async def research(
    session: ToolSession,
    tools: ChatTools,
    *,
    llm: ToolStreamingLlm,
    guard: CostGuard,
    searcher: WebSearcher | None,
    schema: dict[str, Any],
    prompt_bytes: int,
    limits: ResearchLimits,
    clock: Callable[[], float] | None = None,
) -> AsyncIterator[ResearchEvent]:
    """1 ターンを進め、出来事を流す。最後の要素は必ず `ResearchDone`（例外はそのまま投げる）。"""
    now = clock or asyncio.get_running_loop().time
    deadline = now() + limits.seconds
    turn = _Turn(
        allowance=research_allowance(guard, llm, searcher, prompt_bytes, limits),
        history_bytes=prompt_bytes,
    )
    for _ in range(limits.max_steps):
        if turn.calls >= limits.max_calls or now() >= deadline:
            break
        can_search = not turn.private_seen and turn.searches < limits.searches_max
        specs = tools.specs(allow_search=can_search)
        step_worst = guard.estimate(llm.model, turn.history_bytes, llm.step_output_tokens)
        if not turn.fits(step_worst):
            break
        for spec in specs:
            turn.declared.setdefault(spec.name, spec)
        try:
            async with asyncio.timeout(min(limits.step_timeout, max(deadline - now(), 0.0))):
                step = await session.step(specs)
        except TimeoutError:
            # 使用量が分からない: その歩の最悪の額を数えて、調べるのをやめる
            turn.spent_usd += step_worst
            turn.extra_usd += step_worst
            logger.warning("会話の調査の歩が時間切れになった")
            break
        turn.input_tokens += step.input_tokens
        turn.output_tokens += step.output_tokens
        turn.spent_usd += guard.estimate(llm.model, step.input_tokens, step.output_tokens)
        if step.thoughts:
            yield Thought(step.thoughts.rstrip() + "\n\n")
        if not step.calls:
            break
        turn.history_bytes += _STEP_OVERHEAD_BYTES
        allowed = {s.name for s in specs}
        results: list[ToolResult] = []
        for call in step.calls:
            output = _refusal(call, turn, allowed, limits, guard, searcher, now() >= deadline)
            if output is None:
                yield ToolActivity(call.name, tools.describe(call))
                output = await _execute(call, tools, turn, limits, guard, searcher, now, deadline)
            turn.history_bytes += len(output.encode()) + _CALL_OVERHEAD_BYTES
            results.append(ToolResult(call, output))
        session.add_results(results)
    final_tools = list(turn.declared.values())
    async for event in session.stream_final(schema, final_tools, FINAL_REQUEST):
        if isinstance(event, JsonGeneration):
            gen = JsonGeneration(
                event.text,
                turn.input_tokens + event.input_tokens,
                turn.output_tokens + event.output_tokens,
                event.truncated,
            )
            yield ResearchDone(
                gen=gen,
                extra_usd=turn.extra_usd,
                sources=list(turn.sources.values()),
                search_suggestions=turn.suggestions,
                tool_counts=dict(turn.counts),
            )
            return
        yield event
    raise RuntimeError("最後の返答の使用量が届かなかった")


def _refusal(
    call: ToolCall,
    turn: _Turn,
    allowed: set[str],
    limits: ResearchLimits,
    guard: CostGuard,
    searcher: WebSearcher | None,
    expired: bool,
) -> str | None:
    """実行せずに断る呼出しなら、その結果の文。実行してよければ None。"""
    turn.counts[call.name] += 1
    turn.calls += 1
    if turn.calls > limits.max_calls or expired:
        return _LIMIT_REACHED
    if call.name == "web_search":
        if turn.private_seen or "web_search" not in allowed:
            return _SEARCH_CLOSED
        if searcher is None or turn.searches >= limits.searches_max:
            return _LIMIT_REACHED
        if not turn.fits(_search_worst(guard, searcher)):
            return _LIMIT_REACHED
    elif call.name not in allowed:
        return "エラー: その関数は今は使えません"
    return None


async def _execute(
    call: ToolCall,
    tools: ChatTools,
    turn: _Turn,
    limits: ResearchLimits,
    guard: CostGuard,
    searcher: WebSearcher | None,
    now: Callable[[], float],
    deadline: float,
) -> str:
    is_search = call.name == "web_search"
    if is_search:
        turn.searches += 1
    outcome: ToolOutcome | None
    try:
        async with asyncio.timeout(min(limits.tool_timeout, max(deadline - now(), 0.0))):
            outcome = await tools.run(call)
    except TimeoutError:
        outcome = None
    if is_search and searcher is not None and (outcome is None or outcome.search_failed):
        # 時間切れ・例外の検索は課金されたか分からない: 精算でもその検索の最悪額を残す
        worst = _search_worst(guard, searcher)
        turn.spent_usd += worst
        turn.extra_usd += worst
    if outcome is None:
        return _TIMED_OUT
    if outcome.search is not None and searcher is not None:
        cost = guard.estimate(
            searcher.model, outcome.search.input_tokens, outcome.search.output_tokens
        ) + guard.search_cost(outcome.search.query_count)
        turn.spent_usd += cost
        turn.extra_usd += cost
    size = len(outcome.output.encode())
    if turn.result_bytes + size > limits.results_max_bytes:
        return _LIMIT_REACHED
    turn.result_bytes += size
    rendered = outcome.search.rendered_content if outcome.search is not None else None
    if rendered and len(turn.suggestions) < SEARCH_SUGGESTIONS_MAX:
        # 検索の結果をモデルに渡したら、Google 検索の候補を表示する（表示の要件）
        if len(rendered.encode()) <= SEARCH_SUGGESTION_MAX_BYTES:
            turn.suggestions.append(rendered)
        else:
            logger.warning("検索の候補の表示が大きすぎるので残さない")
    if outcome.ok:
        if call.name in PRIVATE_READS:
            turn.private_seen = True
        turn.add_sources(outcome.sources)
    return outcome.output


__all__ = [
    "ChatResearch",
    "ResearchDone",
    "ResearchEvent",
    "ResearchLimits",
    "ToolActivity",
    "final_worst",
    "research",
    "research_allowance",
    "turn_budget",
]
