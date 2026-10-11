"""知識の会話の「調べてから答える」ストリーム。実 PostgreSQL・台本のツール LLM・偽物のリポジトリ。

確かめること: 思考の要約と調べていることの出来事、承認済みの知識の読み取り（今の bundle だけ）、
返答に残る出典と検索の候補の表示、監査に関数の種類と回数だけが残ること、予約がターンの予算で
1 回だけ（発言数は 1）・実費（検索の費用を含む）で精算されること。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.llm.scripted import ScriptedSearch, ScriptedToolLlm
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.domain.knowledge import ArtifactScope, InstructionsContent
from speclino.ports.repo_reader import RepoTree, RepoTreeEntry
from speclino.ports.text_llm import SearchAnswer, SearchSource, ToolCall
from speclino.services import knowledge_chat as kc
from speclino.services import knowledge_registry as kr
from speclino.services.cost_guard import SEARCH_QUERY_USD, CostGuard, InMemoryLedger
from speclino.services.knowledge_chat_budget import ChatBudget, ChatLimits
from speclino.services.knowledge_chat_research import ChatResearch
from tests.integration.services.conftest import register_repo, repo_id_of
from tests.integration.services.test_knowledge_registry import stub_compiler

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]
MODEL = "scripted"
HEAD = "b" * 40
FINAL = json.dumps(
    {"explanation": "既存の指示と食い違わない形にしました", "proposal": {"body": "# 指示\n"}},
    ensure_ascii=False,
)


class Reader:
    def __init__(self, repo: str) -> None:
        self.repo = repo

    async def repository_id(self, repo: str) -> int | None:
        return repo_id_of(repo)

    async def branch_head(self, repo: str, branch: str) -> str | None:
        return HEAD

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        return RepoTree([RepoTreeEntry("src/app.py", "100644", "c" * 40, 12)])

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        return b"print('hi')\n"


@dataclass
class World:
    engine: AsyncEngine
    slug: str
    repo: str
    deps: kc.ChatDeps
    llm: ScriptedToolLlm
    search: ScriptedSearch


@pytest_asyncio.fixture
async def world(engine: AsyncEngine) -> AsyncIterator[World]:
    slug = f"e2e-kr-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"owner1": "owner", "alice": "member"}, slug)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        rev = await kr.create_draft(
            s,
            workspace=slug,
            kind="instructions",
            name="dev-guide",
            content=InstructionsContent(
                kind="instructions",
                body="# 開発の指示\n- テストを先に書く\n",
                scope=ArtifactScope(workspace=True),
            ),
            author="alice",
        )
    async with factory() as s, s.begin():
        await kr.approve(
            s,
            artifact_id=rev.artifact_id,
            expected_revision=rev.revision,
            expected_hash=rev.content_hash,
            approver="owner1",
            compiler=stub_compiler,
        )
    guard = CostGuard(
        InMemoryLedger([]),
        Decimal("1000"),
        per_run_limit_usd=Decimal("0"),
        pricing={MODEL: (Decimal("1"), Decimal("1"))},
    )
    llm = ScriptedToolLlm(
        [
            [ToolCall("web_search", {"query": "AGENTS.md の書き方"}, id="w")],
            [
                ToolCall("knowledge_list", {}, id="k1"),
                ToolCall("knowledge_read", {"kind": "instructions", "name": "dev-guide"}, id="k2"),
                ToolCall("repo_read", {"repo": repo, "path": "src/app.py"}, id="r"),
            ],
        ],
        FINAL,
    )
    search = ScriptedSearch(
        SearchAnswer(
            "AGENTS.md は短く書く",
            [SearchSource("agents.md", "https://agents.example/")],
            rendered_content="<style>.c{}</style><div class=c>chips</div>",
            query_count=3,
        )
    )
    deps = kc.ChatDeps(
        factory,
        llm,  # type: ignore[arg-type]  # ToolStreamingLlm だけを実装した台本
        ChatBudget(factory, guard, ChatLimits()),
        guard,
        research=ChatResearch(reader=Reader(repo), searcher=search),
    )
    try:
        yield World(engine, slug, repo, deps, llm, search)
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                sql_text("DELETE FROM cost_ledger WHERE workspace = :w"), {"w": slug}
            )


async def test_research_stream_saves_sources_and_settles(world: World) -> None:
    async with world.deps.session_factory() as s, s.begin():
        chat = await kc.create_chat(s, workspace=world.slug, owner="alice", kind="instructions")
    events = [
        ev
        async for ev in kc.stream_message(
            world.deps,
            workspace=world.slug,
            owner="alice",
            chat_id=chat.id,
            text="指示書を直したい",
            idempotency_key="key-research-1",
            editor_text="# 指示\n",
        )
    ]
    types_ = [e.type for e in events]
    assert types_[0] == "status" and types_[-1] == "done"
    assert "thought" in types_
    tools = [e.text for e in events if e.type == "tool"]
    assert tools == [
        "Web を検索しています: AGENTS.md の書き方",
        "承認済みの知識の一覧を見ています",
        "承認済みの指示書（AGENTS.md / CLAUDE.md）「dev-guide」を読んでいます",
        f"リポジトリ {world.repo} の src/app.py を読んでいます",
    ]
    # プロンプトに深掘りの規則と読めるリポジトリが入る
    assert "深掘りと調査" in world.llm.prompts[0] and world.repo in world.llm.prompts[0]
    # 知識の結果は承認済みの本文（区切りの中）
    outputs = {r.call.id: r.output for batch in world.llm.results for r in batch}
    assert "dev-guide（改訂 1）" in outputs["k1"]
    assert "テストを先に書く" in outputs["k2"] and outputs["k2"].count("<<<") == 1

    turn = events[-1].turn
    assert turn is not None and turn.reply is not None
    sources = turn.reply.sources or []
    assert [s.kind for s in sources] == ["web", "knowledge", "repo"]
    assert sources[1].ref == "instructions:dev-guide@1"
    assert sources[2].url == f"https://github.com/{world.repo}/blob/{HEAD}/src/app.py"
    assert turn.reply.search_suggestions == ["<style>.c{}</style><div class=c>chips</div>"]

    async with world.deps.session_factory() as s:
        saved = await kc.get_chat(s, workspace=world.slug, owner="alice", chat_id=chat.id)
        ledger = CostLedgerRepository(s)
        sent = await ledger.count_reservations(
            world.deps.today(), uid="alice", purpose="chat_message"
        )
        used = await ledger.total_for_day(world.deps.today(), workspace=world.slug)
        audit = (
            await s.execute(
                sql_text(
                    "SELECT detail::text FROM audit_events WHERE target LIKE :t "
                    "AND action = 'knowledge.chat.message'"
                ),
                {"t": f"{world.slug}/chat/%"},
            )
        ).scalar_one()
    assert saved.messages[-1].sources == turn.reply.sources
    assert sent == 1  # 発言の数は 1（内部の歩を別の予約にしない）
    # 3 歩＋最後の返答の (1000 + 200) トークン × 1 USD / 1M、検索 3 件
    assert used == Decimal(4 * 1200) / 10**6 + 3 * SEARCH_QUERY_USD
    detail = json.loads(audit)
    assert detail["tools"] == {
        "knowledge_list": 1,
        "knowledge_read": 1,
        "repo_read": 1,
        "web_search": 1,
    }
    assert "dev-guide" not in audit and "src/app.py" not in audit and "AGENTS" not in audit
