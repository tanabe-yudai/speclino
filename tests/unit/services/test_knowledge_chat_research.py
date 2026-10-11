"""知識の会話の調査: 関数のループ（上限・呼出しと結果の対応・web_search の宣言外し・費用）と、
リポジトリを読む関数のパスの検査。

LLM は台本（`ScriptedToolLlm`）、リポジトリは偽物の `RepoReader`、検索は `ScriptedSearch`。
知識の関数は DB を使うので integration で確かめる。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from speclino.adapters.llm.scripted import ScriptedSearch, ScriptedToolLlm
from speclino.ports.repo_reader import RepoTree, RepoTreeEntry
from speclino.ports.text_llm import (
    SearchAnswer,
    SearchSource,
    Thought,
    ToolCall,
)
from speclino.services.cost_guard import SEARCH_QUERY_USD, CostGuard, InMemoryLedger
from speclino.services.knowledge_chat_research import (
    ResearchDone,
    ResearchLimits,
    ToolActivity,
    research,
    turn_budget,
)
from speclino.services.knowledge_chat_tools import ChatTools, RegisteredRepo, checked_path

REPO = "acme/app"
HEAD = "a" * 40
FINAL = '{"explanation": "調べました"}'


class FakeReader:
    def __init__(self, files: dict[str, bytes], modes: dict[str, str] | None = None) -> None:
        self.files = files
        self.modes = modes or {}
        self.head_calls = 0
        self.tree_calls = 0
        self.blob_shas: list[str] = []
        self.repo_id = 7

    async def repository_id(self, repo: str) -> int | None:
        return self.repo_id

    async def branch_head(self, repo: str, branch: str) -> str | None:
        self.head_calls += 1
        return HEAD

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        assert commit_sha == HEAD
        self.tree_calls += 1
        return RepoTree(
            [
                RepoTreeEntry(p, self.modes.get(p, "100644"), f"{i:040x}", len(b))
                for i, (p, b) in enumerate(self.files.items(), start=1)
            ]
        )

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        self.blob_shas.append(blob_sha)
        idx = int(blob_sha, 16) - 1
        return list(self.files.values())[idx]


def _tools(reader: FakeReader | None = None, searcher: ScriptedSearch | None = None) -> ChatTools:
    return ChatTools(
        session_factory=None,  # type: ignore[arg-type]  # 知識の関数はここでは呼ばない
        workspace="team",
        repos=[RegisteredRepo(REPO, "main", 7)],
        reader=reader or FakeReader({"src/app.py": b"print('hi')\n"}),
        searcher=searcher,
    )


def _guard() -> CostGuard:
    return CostGuard(
        InMemoryLedger([]), Decimal("1000"), pricing={"scripted": (Decimal("1"), Decimal("1"))}
    )


async def _run(
    llm: ScriptedToolLlm,
    tools: ChatTools,
    *,
    searcher: ScriptedSearch | None = None,
    limits: ResearchLimits | None = None,
    guard: CostGuard | None = None,
    clock: Any = None,
) -> list[Any]:
    session = llm.open("依頼")
    return [
        e
        async for e in research(
            session,
            tools,
            llm=llm,
            guard=guard or _guard(),
            searcher=searcher,
            schema={"type": "object"},
            prompt_bytes=1000,
            limits=limits or ResearchLimits(),
            clock=clock,
        )
    ]


def _read(path: str, cid: str = "c") -> ToolCall:
    return ToolCall("repo_read", {"repo": REPO, "path": path}, id=cid)


async def test_loop_runs_calls_and_answers_each_id() -> None:
    llm = ScriptedToolLlm(
        [[_read("src/app.py", "c1"), ToolCall("repo_tree", {"repo": REPO}, id="c2")]], FINAL
    )
    events = await _run(llm, _tools())
    activities = [e.text for e in events if isinstance(e, ToolActivity)]
    assert activities == [
        "リポジトリ acme/app の src/app.py を読んでいます",
        "リポジトリ acme/app のファイルの一覧を見ています",
    ]
    assert any(isinstance(e, Thought) for e in events)
    [batch] = llm.results
    assert [r.call.id for r in batch] == ["c1", "c2"]
    assert "print('hi')" in batch[0].output and batch[0].output.count("<<<") == 1
    done = events[-1]
    assert isinstance(done, ResearchDone)
    assert done.gen.text == FINAL
    # 2 歩（調べる 1 歩＋何も呼ばない 1 歩）と最後の返答のトークンの合計
    assert (done.gen.input_tokens, done.gen.output_tokens) == (3000, 600)
    assert [s.kind for s in done.sources] == ["repo"]
    assert done.sources[0].ref == f"{REPO}@{HEAD}:src/app.py"
    assert done.tool_counts == {"repo_read": 1, "repo_tree": 1}


async def test_call_limit_answers_extra_calls_and_stops() -> None:
    calls = [_read("src/app.py", f"c{i}") for i in range(10)]
    llm = ScriptedToolLlm([calls, [_read("src/app.py", "late")]], FINAL)
    reader = FakeReader({"src/app.py": b"x\n"})
    events = await _run(llm, _tools(reader))
    [batch] = llm.results  # 上限に達したので次の歩へ進まない
    assert len(batch) == 10
    assert sum("上限" in r.output for r in batch) == 2
    assert sum(isinstance(e, ToolActivity) for e in events) == 8
    assert reader.head_calls == 1 and reader.tree_calls == 1  # 先端はターンの中で固定


async def test_step_limit() -> None:
    llm = ScriptedToolLlm([[_read("src/app.py", f"s{i}")] for i in range(5)], FINAL)
    await _run(llm, _tools(), limits=ResearchLimits(max_steps=2))
    assert len(llm.declared) == 2 and len(llm.results) == 2


async def test_deadline_stops_research() -> None:
    times = iter([0.0, 0.0, 500.0, 500.0, 500.0, 500.0])
    llm = ScriptedToolLlm([[_read("src/app.py")], [_read("src/app.py", "x")]], FINAL)
    await _run(llm, _tools(), clock=lambda: next(times, 500.0))
    assert len(llm.results) == 1
    assert "上限" in llm.results[0][0].output


async def test_web_search_is_withdrawn_after_private_read() -> None:
    answer = SearchAnswer(
        "公開の情報",
        [SearchSource("Doc", "https://docs.example/a")],
        rendered_content="<div>chips</div>",
        query_count=2,
    )
    searcher = ScriptedSearch(answer)
    llm = ScriptedToolLlm(
        [
            [ToolCall("web_search", {"query": "pytest fixture"}, id="w1")],
            [_read("src/app.py", "r1")],
            [ToolCall("web_search", {"query": "社内の秘密"}, id="w2")],
        ],
        FINAL,
    )
    events = await _run(llm, _tools(searcher=searcher), searcher=searcher)
    assert "web_search" in llm.declared[0]
    assert "web_search" in llm.declared[1]
    assert "web_search" not in llm.declared[2]  # 読んだ後は宣言から外す
    assert "Web を検索できません" in llm.results[2][0].output  # 呼ばれても断る
    assert searcher.queries == ["pytest fixture"]
    assert llm.final_declared is not None and "web_search" in llm.final_declared
    done = events[-1]
    assert isinstance(done, ResearchDone)
    assert done.search_suggestions == ["<div>chips</div>"]
    assert {s.kind for s in done.sources} == {"web", "repo"}
    # 検索の費用（問い合わせ 2 件。トークンは台本の検索では 0）は精算に足す
    assert done.extra_usd == 2 * SEARCH_QUERY_USD


async def test_budget_limits_research() -> None:
    """調べる歩の最悪の額が枠に収まらなければ、調べずに最後の返答を求める。"""
    llm = ScriptedToolLlm([[_read("src/app.py")]], FINAL)
    tiny = ResearchLimits(budget_steps=0, budget_searches=0)
    events = await _run(llm, _tools(), limits=tiny)
    assert llm.declared == [] and llm.results == []
    assert isinstance(events[-1], ResearchDone)


def test_turn_budget_covers_final_and_steps() -> None:
    guard = _guard()
    llm = ScriptedToolLlm([], FINAL)
    limits = ResearchLimits()
    plain = turn_budget(guard, llm, None, 1000, limits)
    with_search = turn_budget(guard, llm, ScriptedSearch(SearchAnswer("")), 1000, limits)
    final_only = turn_budget(guard, llm, None, 1000, ResearchLimits(budget_steps=0))
    assert final_only < plain < with_search
    # 最後の返答は、結果の上限まで膨らんだ履歴と出力の上限で見積もる
    assert final_only >= Decimal(1000 + limits.results_max_bytes + llm.max_output_tokens) / 10**6


# ------------------------------------------------------------------ repo_read のパス
@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "src/../../x",
        "/abs/path",
        "a//b",
        "./a",
        "a\\b",
        ".env",
        "config/.env.production",
        "deploy/key.pem",
        "home/.ssh/config",
        "id_rsa",
        "infra/prod.tfvars",
        "",
    ],
)
def test_checked_path_rejects(path: str) -> None:
    from speclino.services.knowledge_chat_tools import ToolRefused

    with pytest.raises(ToolRefused):
        checked_path(path)


async def _read_one(reader: FakeReader, path: str, repo: str = REPO) -> str:
    tools = _tools(reader)
    outcome = await tools.run(ToolCall("repo_read", {"repo": repo, "path": path}))
    return outcome.output


async def test_repo_read_refusals() -> None:
    reader = FakeReader(
        {
            "link.py": b"target",
            "bin.dat": b"\x00\x01\x02",
            "latin.txt": "caf\xe9".encode("latin-1"),
            "ok.py": b"token = 'ghp_" + b"A" * 36 + b"'\n",
        },
        modes={"link.py": "120000"},
    )
    assert "通常のファイルではない" in await _read_one(reader, "link.py")
    assert "バイナリ" in await _read_one(reader, "bin.dat")
    assert "バイナリ" in await _read_one(reader, "latin.txt")
    assert "ありません" in await _read_one(reader, "missing.py")
    assert "登録済みのリポジトリではありません" in await _read_one(reader, "ok.py", "other/repo")
    masked = await _read_one(reader, "ok.py")
    assert "ghp_" not in masked and "伏せた" in masked
    # 読んだのは木の 40 桁の SHA だけ（モデルのパスを URL に入れない）
    assert all(len(s) == 40 for s in reader.blob_shas)


async def test_repo_read_refuses_renamed_repository() -> None:
    reader = FakeReader({"ok.py": b"x"})
    reader.repo_id = 99  # 登録したリポジトリ ID と違う
    assert "読めません" in await _read_one(reader, "ok.py")


async def test_repo_read_line_range_and_cut() -> None:
    body = "".join(f"line {i}\n" for i in range(1, 20001)).encode()
    reader = FakeReader({"big.txt": body})
    tools = _tools(reader)
    part = await tools.run(
        ToolCall("repo_read", {"repo": REPO, "path": "big.txt", "start_line": 3, "end_line": 4})
    )
    assert "line 3\nline 4\n" in part.output and "line 5" not in part.output
    whole = await tools.run(ToolCall("repo_read", {"repo": REPO, "path": "big.txt"}))
    assert "切りました" in whole.output
    assert len(whole.output.encode()) < 34 * 1024


async def test_repo_tree_hides_secret_paths() -> None:
    reader = FakeReader({"src/a.py": b"", ".env": b"", "src/b.py": b""})
    tools = _tools(reader)
    out = (await tools.run(ToolCall("repo_tree", {"repo": REPO, "path_prefix": "src/"}))).output
    assert "src/a.py" in out and "src/b.py" in out and ".env" not in out


async def test_failed_search_keeps_its_worst_cost() -> None:
    """例外で終わった検索は課金されたか分からないので、その検索の最悪額を精算に残す。"""

    class Broken(ScriptedSearch):
        async def search(self, query: str) -> SearchAnswer:
            raise RuntimeError("provider down")

    searcher = Broken(SearchAnswer(""))
    llm = ScriptedToolLlm([[ToolCall("web_search", {"query": "q"}, id="w")]], FINAL)
    guard = _guard()
    events = await _run(llm, _tools(searcher=searcher), searcher=searcher, guard=guard)
    assert "失敗" in llm.results[0][0].output
    done = events[-1]
    assert isinstance(done, ResearchDone)
    worst = guard.estimate("scripted", 4 * 1024, searcher.max_output_tokens) + guard.search_cost(
        searcher.max_queries
    )
    assert done.extra_usd == worst


async def test_repository_without_github_id_is_not_offered_or_read() -> None:
    """ID の無い登録は、名前の使い回しを確かめられないので扱わない（fail-closed）。"""
    reader = FakeReader({"ok.py": b"x"})
    tools = ChatTools(
        session_factory=None,  # type: ignore[arg-type]
        workspace="team",
        repos=[RegisteredRepo(REPO, "main", None)],
        reader=reader,
        searcher=None,
    )
    assert tools.repos == []
    assert "repo_read" not in [s.name for s in tools.specs(allow_search=False)]
    out = await tools.run(ToolCall("repo_read", {"repo": REPO, "path": "ok.py"}))
    assert not out.ok and "登録済みのリポジトリではありません" in out.output
    assert reader.head_calls == 0 and reader.blob_shas == []
