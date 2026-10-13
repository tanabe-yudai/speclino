"""ReviewRunService の E2E（Postgres + ローカル git + インプロセスキュー。GitHub なし）。

`SPECLINO_DATABASE_URL` が未設定なら skip。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.agent.permissions import AgentPermissions
from speclino.db.repositories import (
    ApprovalRepository,
    FindingRepository,
    ProjectRepository,
    ReviewRunRepository,
    RunEventRepository,
)
from speclino.ports.text_llm import JsonGeneration, TextLlm
from speclino.reporter.check_report import COMMENT_MARKER
from speclino.services.investigation import InvestigationResult
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.services.spec_pr_bot import SPEC_BOT_MARKER
from speclino.spec import load_spec_ir
from speclino.spec.spec_validator import rule_content_hash
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of


def _base_rule(rule_id: str):  # type: ignore[no-untyped-def]
    """base（examples/orders の承認済み Spec IR）から Rule を引く。"""
    spec = load_spec_ir(
        Path(__file__).resolve().parents[3] / "examples/orders/specs/ir/cancel_order.spec-ir.json"
    )
    return next(r for r in spec.rules if r.id == rule_id)


REPO = Path(__file__).resolve().parents[3]
ORDERS = REPO / "examples" / "orders"
_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_demo_repo(root: Path) -> tuple[str, str, str]:
    """examples/orders を根に持つ git リポジトリ（base=正解、head=管理者のみ、head2=仕様削除）。"""
    root.mkdir()
    app_dst = root / "examples" / "orders" / "app"
    app_dst.mkdir(parents=True)
    (root / "examples" / "__init__.py").write_text("")
    (root / "examples" / "orders" / "__init__.py").write_text("")
    for name in ("__init__.py", "models.py", "inventory.py", "audit.py", "service.py"):
        shutil.copy(ORDERS / "app" / name, app_dst / name)
    shutil.copytree(ORDERS / ".speclino", root / ".speclino")
    shutil.copytree(ORDERS / "specs", root / "specs")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    # 開発者の git 設定（コミット署名）に左右されないようにする
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base: correct implementation")
    base = git(root, "rev-parse", "HEAD")
    svc = app_dst / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "    inventory.restore(order.items)\n",
            "    if user.is_admin:\n        inventory.restore(order.items)\n",
        )
    )
    git(root, "commit", "-qam", "head: restore only for admins")
    head = git(root, "rev-parse", "HEAD")
    # head2: 仕様（R-CANCEL-04）を IR から削除して通そうとする
    ir_path = root / "specs" / "ir" / "cancel_order.spec-ir.json"
    data = json.loads(ir_path.read_text())
    data["rules"] = [r for r in data["rules"] if r["id"] != "R-CANCEL-04"]
    ir_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    git(root, "commit", "-qam", "head2: drop R-CANCEL-04")
    head2 = git(root, "rev-parse", "HEAD")
    return base, head, head2


@pytest_asyncio.fixture
async def service(
    engine: AsyncEngine, tmp_path: Path
) -> tuple[ReviewRunService, LocalCodeHost, str, str, str, str]:
    repo_name = f"e2e/orders-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo_name, {})
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(
        repos={repo_name: tmp_path / "repo"}, repo_ids={repo_name: repo_id_of(repo_name)}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True)
    svc = ReviewRunService(
        session_factory=factory,
        code_host=host,
        queue=queue,
        blobs=FileSystemBlobStore(tmp_path / "blobs"),
        config=ReviewRunConfig(agent_enabled=False, details_url_base="http://localhost/runs"),
    )
    holder["svc"] = svc
    return svc, host, repo_name, base, head, head2


async def test_pr_run_finds_violation_and_publishes_failure_check(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str], engine: AsyncEngine
) -> None:
    svc, host, repo, base, head, _ = service
    run_id = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=1,
        trigger="pull_request",
    )
    async with async_sessionmaker(engine)() as s:
        run = await ReviewRunRepository(s).get(run_id)
        assert run is not None
        assert run.exec_state == "completed" and run.gate_status == "fail"
        assert run.check_run_id == 1 and run.artifacts_uri and run.manifest
        findings = await FindingRepository(s).list_for_run(run_id)
        by_id = {f.requirement_id: f for f in findings}
        assert by_id["R-CANCEL-04"].verdict == "VIOLATED"
        events = await RunEventRepository(s).list_for_run(run_id)
        assert [e.type for e in events][:2] == ["state", "state"] and events[-1].type == "state"
        assert events[-1].payload["publish"] == "publish"
    check = host.checks[1]
    assert (check["status"], check["conclusion"]) == ("completed", "failure")
    assert (
        "R-CANCEL-04" in check["text"] and check["details_url"] == f"http://localhost/runs/{run_id}"
    )
    assert any(a["annotation_level"] == "failure" for a in check["annotations"])


async def test_removed_rule_without_approval_fails_then_passes_after_approve_spec(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str], engine: AsyncEngine
) -> None:
    svc, host, repo, base, _, head2 = service
    run_id = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head2,
        base_sha=base,
        pr_number=2,
        trigger="pull_request",
    )
    async with async_sessionmaker(engine)() as s:
        findings = await FindingRepository(s).list_for_run(run_id)
        removed = next(f for f in findings if f.requirement_id == "R-CANCEL-04")
        assert (removed.verdict, removed.category) == ("REVIEW", "stale_approval")
    assert host.checks[1]["conclusion"] == "failure"
    # 承認者が /speclino approve-spec removed:<hash> した後の再実行

    base_hash = rule_content_hash(_base_rule("R-CANCEL-04"))
    async with async_sessionmaker(engine)() as s, s.begin():
        project = await ProjectRepository(s).get_by_repo(repo)
        assert project is not None
        await ApprovalRepository(s).add(
            project_id=project.id,
            kind="approve_spec",
            target=f"removed:{base_hash}",
            content_hash="sha256:x",
            approver="tanabe-yudai",
            comment_url=None,
        )
    run2 = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head2,
        base_sha=base,
        pr_number=2,
        trigger="rerun",
    )
    async with async_sessionmaker(engine)() as s:
        run = await ReviewRunRepository(s).get(run2)
        assert run is not None and run.gate_status == "pass"


async def test_superseded_run_is_not_published(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str],
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    svc, host, repo, base, head, head2 = service
    # 即時実行を止め、2 つの run を queued にしてから古い方を実行する
    queue = svc.queue
    assert isinstance(queue, InProcessQueue)
    queue.immediate = False
    old = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=3,
        trigger="pull_request",
    )
    new = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head2,
        base_sha=base,
        pr_number=3,
        trigger="pull_request",
    )
    await queue.drain()
    async with async_sessionmaker(engine)() as s:
        runs = ReviewRunRepository(s)
        old_run, new_run = await runs.get(old), await runs.get(new)
        assert old_run is not None and new_run is not None
        # 古い run は queued のまま superseded になり、実行権を取れないので Check も作らない
        assert old_run.exec_state == "superseded" and old_run.check_run_id is None
        assert new_run.exec_state == "completed" and new_run.check_run_id is not None
    assert all(c["head_sha"] == head2 for c in host.checks.values())


async def test_agent_failure_does_not_fail_the_run(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """証拠収集が落ちても run は完了し、未解決のままゲートが失敗する。

    「乗っ取られた場合でも最悪の結果は証拠不足 → 未検証 → ゲート失敗」という方針を、
    エージェントの実行障害にも適用する。本番では Vertex AI の 429 で run 全体が
    `failed` になり、判定結果が一切表示されなかった。
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr("speclino.services.review_run_service.investigate", _explode)

    repo = f"e2e/agent-fail-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    # 対応外のループで UNKNOWN を作り、エージェントを起動させる
    root = tmp_path / "repo"
    base, _head, _head2 = make_demo_repo(root)
    # head2 は Spec IR から要求を消した版なので、仕様は base のものに戻してから
    # ループを入れる（消えた要求に対する UNKNOWN は出ないため）
    git(root, "checkout", base, "--", "specs")
    svc = root / "examples" / "orders" / "app" / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "    if user.is_admin:\n        inventory.restore(order.items)\n",
            "    for item in order.items:\n        inventory.restore(item)\n",
        )
    )
    git(root, "commit", "-qam", "head3: loop")
    head3 = git(root, "rev-parse", "HEAD")

    host = LocalCodeHost(repos={repo: root}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    service = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=True),
    )
    holder["svc"] = service
    run_id = await service.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head3,
        base_sha=base,
        pr_number=1,
        trigger="test",
    )
    async with factory() as session:
        run = await ReviewRunRepository(session).get(run_id)
        assert run is not None
        assert run.exec_state == "completed", "エージェントの失敗で run を落とさない"
        assert run.gate_status == "fail", "未解決が残るのでゲートは失敗"
        events = await RunEventRepository(session).list_for_run(run_id)
        findings = await FindingRepository(session).list_for_run(run_id)
    assert any(e.type == "agent.error" for e in events), "失敗はイベントに残す"
    assert any(f.payload["verdict"] == "UNKNOWN" for f in findings), "判定は表示できる"


async def test_gate_comment_is_updated_not_duplicated(engine: AsyncEngine, tmp_path: Path) -> None:
    """再実行しても PR コメントは増えず、同じコメントが更新される。"""
    repo = f"e2e/comment-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    service = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=False),
    )
    holder["svc"] = service
    for _ in range(2):
        await service.create_run(
            project_id=await project_id_of(engine, repo),
            head_sha=head,
            base_sha=base,
            pr_number=5,
            trigger="test",
        )
    gate_comments = [c for c in host.comments if COMMENT_MARKER in c["body"]]
    assert len(gate_comments) == 1, [c["body"][:60] for c in host.comments]
    assert "SpecLino / gate" in gate_comments[0]["body"]


async def test_child_run_rejects_a_parent_from_another_project(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str], engine: AsyncEngine
) -> None:
    """子 run の親は同じ project の run に限る（別リポジトリの回答を取り込ませない。S52）。"""
    svc, host, repo, base, head, _ = service
    parent = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=1,
        trigger="pull_request",
    )
    other = f"{repo}-other"
    host.repos[other] = host.repos[repo]
    await register_repo(engine, other, {})
    with pytest.raises(ValueError, match="の run ではない"):
        await svc.create_run(
            project_id=await project_id_of(engine, other),
            head_sha=head,
            base_sha=base,
            pr_number=1,
            trigger="comment",
            parent_run_id=parent,
        )


async def test_a_run_is_not_executed_when_the_repository_id_changed(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str], engine: AsyncEngine
) -> None:
    """S53: 名前で取れるリポジトリの ID が登録時と違えば（改名後に旧名が再利用された）、
    スナップショットを取らず、Check も作らずに run を失敗させる。"""
    svc, host, repo, base, head, _ = service
    host.repo_ids[repo] = repo_id_of(repo) + 1
    run_id = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=1,
        trigger="pull_request",
    )
    async with async_sessionmaker(engine)() as s:
        run = await ReviewRunRepository(s).get(run_id)
        assert run is not None and run.exec_state == "failed" and run.gate_status is None
        events = await RunEventRepository(s).list_for_run(run_id)
    assert "リポジトリ ID が登録時と違う" in events[-1].payload["error"]
    assert host.checks == {} and host.comments == []


@pytest.mark.parametrize(
    ("project_settings", "expect_called", "expect_test_run"),
    [
        ({}, True, True),
        ({"agent_enabled": False}, False, None),
        ({"test_run_enabled": False}, True, False),
        ({"fail_on": ["VIOLATED"]}, False, None),  # 型に合わない保存値は安全側（止める）
    ],
)
async def test_project_settings_gate_agent_and_test_run(
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_settings: dict[str, object],
    expect_called: bool,
    expect_test_run: bool | None,
) -> None:
    """S47: project の設定でエージェントと test_run を止められる（サーバ全体の設定と AND）。"""
    calls: list[AgentPermissions] = []

    async def _fake(ctx, permissions, model, cost_ok):  # type: ignore[no-untyped-def]
        calls.append(permissions)
        return InvestigationResult(False, ctx.findings, reason="fake")

    monkeypatch.setattr("speclino.services.review_run_service.investigate", _fake)
    repo = f"e2e/settings-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    async with engine.begin() as conn:
        await conn.execute(
            sql_text("UPDATE projects SET settings = CAST(:s AS jsonb) WHERE repo_full_name = :r"),
            {"s": json.dumps(project_settings), "r": repo},
        )
    root = tmp_path / "repo"
    base, _head, _head2 = make_demo_repo(root)
    # 対応外のループで UNKNOWN を作り、エージェントが起動する条件にする
    git(root, "checkout", base, "--", "specs")
    svc = root / "examples" / "orders" / "app" / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "    if user.is_admin:\n        inventory.restore(order.items)\n",
            "    for item in order.items:\n        inventory.restore(item)\n",
        )
    )
    git(root, "commit", "-qam", "head3: loop")
    head3 = git(root, "rev-parse", "HEAD")
    host = LocalCodeHost(repos={repo: root}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    service = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=True, permissions=AgentPermissions(test_run_enabled=True)),
    )
    holder["svc"] = service
    run_id = await service.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head3,
        base_sha=base,
        pr_number=1,
        trigger="test",
    )
    async with factory() as session:
        run = await ReviewRunRepository(session).get(run_id)
        assert run is not None and run.exec_state == "completed"
    assert bool(calls) is expect_called
    if expect_called:
        assert calls[0].test_run_enabled is expect_test_run


# ---------------------------------------------------------------- S46 仕様 PR bot
_SPEC_PROPOSAL = json.dumps(
    {
        "rules": [],
        "unextracted": [{"requirement_id": "R-CANCEL-08", "reason": "語彙で表せない"}],
        "ambiguities": [
            {"requirement_id": "R-CANCEL-08", "phrase": "適切に", "question": "何を返すか"}
        ],
    },
    ensure_ascii=False,
)


class _BrokenJsonLlm:
    model = "gemini-3.8-flash"

    async def generate_json(self, prompt: str, schema: dict[str, object]) -> JsonGeneration:
        raise RuntimeError("Gemini の認証情報が無い")


def _spec_bot_service(
    engine: AsyncEngine, tmp_path: Path, repo: str, llm: TextLlm | None
) -> tuple[ReviewRunService, LocalCodeHost, str, str]:
    """base と、仕様 Markdown に要求を 1 つ足した head を持つリポジトリ。"""
    root = tmp_path / "repo"
    base, _head, _head2 = make_demo_repo(root)
    git(root, "checkout", "-q", base)
    git(root, "checkout", "-q", "-b", "spec-pr")
    md = root / "specs" / "orders-cancel.md"
    md.write_text(md.read_text() + "\n### R-CANCEL-08\n\n適切にエラーを返す。\n")
    git(root, "commit", "-qam", "spec: add R-CANCEL-08")
    head = git(root, "rev-parse", "HEAD")
    host = LocalCodeHost(repos={repo: root}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    svc = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=False, spec_llm=llm),
    )
    holder["svc"] = svc
    return svc, host, base, head


async def test_spec_pr_bot_comments_candidates_once_without_touching_the_gate(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    repo = f"e2e/spec-bot-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    llm = ScriptedJsonLlm(_SPEC_PROPOSAL)
    svc, host, base, head = _spec_bot_service(engine, tmp_path, repo, llm)
    project_id = await project_id_of(engine, repo)
    run_ids = [
        await svc.create_run(
            project_id=project_id, head_sha=head, base_sha=base, pr_number=7, trigger="test"
        )
        for _ in range(2)
    ]
    bot = [c for c in host.comments if SPEC_BOT_MARKER in c["body"]]
    assert len(bot) == 1, "push・再実行でコメントを積み上げない"
    body = bot[0]["body"]
    assert "specs/orders-cancel.md" in body and "W_AMBIGUOUS" in body and "W_UNEXTRACTED" in body
    assert "| R-CANCEL-04 | 削除 |" in body, "既存 IR との差分を併記する"
    assert len(llm.prompts) == 1, "入力が同じなら Gemini を呼び直さない"
    assert COMMENT_MARKER not in body, "ゲートのコメントとは別のコメント"

    # 同じ head を bot なし（別 PR）で検証した結果と run・Check が一致する。
    # この head は本文だけを変えて IR を直していないので、run は E_SPEC_HASH_MISMATCH で
    # 入力不正になる。その場合でも候補は出す（仕様 PR の最初の push が主な利用場面）
    svc.config.spec_llm = None
    ref_run = await svc.create_run(
        project_id=project_id, head_sha=head, base_sha=base, pr_number=8, trigger="test"
    )
    async with async_sessionmaker(engine)() as s:
        runs = ReviewRunRepository(s)
        with_bot = await runs.get(run_ids[-1])
        without = await runs.get(ref_run)
        assert with_bot is not None and without is not None
        assert (with_bot.exec_state, with_bot.gate_status) == (
            without.exec_state,
            without.gate_status,
        )
        assert with_bot.check_run_id is not None and without.check_run_id is not None
        events = await RunEventRepository(s).list_for_run(run_ids[0])
        events2 = await RunEventRepository(s).list_for_run(run_ids[1])
    c1, c2 = host.checks[with_bot.check_run_id], host.checks[without.check_run_id]
    assert (c1["conclusion"], c1["title"], c1["summary"]) == (
        c2["conclusion"],
        c2["title"],
        c2["summary"],
    )
    [ev] = [e for e in events if e.type == "spec_bot"]
    assert ev.payload["status"] == "posted"
    [ev2] = [e for e in events2 if e.type == "spec_bot"]
    assert ev2.payload["status"] == "unchanged"
    # 仕様 IR はリポジトリに書かれていない（bot は提案だけ）
    assert git(tmp_path / "repo", "status", "--porcelain") == ""


async def test_spec_pr_bot_says_why_when_gemini_is_unavailable(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    repo = f"e2e/spec-bot-fail-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    svc, host, base, head = _spec_bot_service(engine, tmp_path, repo, _BrokenJsonLlm())
    run_id = await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=9,
        trigger="test",
    )
    [bot] = [c for c in host.comments if SPEC_BOT_MARKER in c["body"]]
    assert "候補を生成できなかった" in bot["body"] and "認証情報が無い" in bot["body"]
    async with async_sessionmaker(engine)() as s:
        events = await RunEventRepository(s).list_for_run(run_id)
    [ev] = [e for e in events if e.type == "spec_bot"]
    assert ev.payload["status"] == "failed", "失敗は次の run で作り直す"


async def test_spec_pr_bot_stays_silent_without_spec_changes(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str], engine: AsyncEngine
) -> None:
    svc, host, repo, base, head, _ = service
    llm = ScriptedJsonLlm(_SPEC_PROPOSAL)
    svc.config.spec_llm = llm
    await svc.create_run(
        project_id=await project_id_of(engine, repo),
        head_sha=head,
        base_sha=base,
        pr_number=10,
        trigger="test",
    )
    assert llm.prompts == []
    assert not any(SPEC_BOT_MARKER in c["body"] for c in host.comments)


class _OvertakenLlm(ScriptedJsonLlm):
    """1 回目の生成中に、同じ PR の新しい run（別の head）を最後まで実行させる。"""

    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.on_first_call: Callable[[], Awaitable[object]] | None = None

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        hook, self.on_first_call = self.on_first_call, None
        if hook is not None:
            await hook()
        return await super().generate_json(prompt, schema)


async def test_stale_run_does_not_overwrite_the_newer_bot_comment(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """生成中に新しい run が完了・投稿したら、古い run は投稿しない。

    古い head の候補で、新しい run のコメントを上書きしない。
    """
    repo = f"e2e/spec-bot-stale-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    llm = _OvertakenLlm(_SPEC_PROPOSAL)
    svc, host, base, head = _spec_bot_service(engine, tmp_path, repo, llm)
    root = tmp_path / "repo"
    md = root / "specs" / "orders-cancel.md"
    md.write_text(md.read_text() + "\n### R-CANCEL-09\n\n速やかに通知する。\n")
    git(root, "commit", "-qam", "spec: add R-CANCEL-09")
    newer = git(root, "rev-parse", "HEAD")
    project_id = await project_id_of(engine, repo)

    async def newer_run() -> object:
        return await svc.create_run(
            project_id=project_id, head_sha=newer, base_sha=base, pr_number=12, trigger="push"
        )

    llm.on_first_call = newer_run
    old_run = await svc.create_run(
        project_id=project_id, head_sha=head, base_sha=base, pr_number=12, trigger="test"
    )
    [bot] = [c for c in host.comments if SPEC_BOT_MARKER in c["body"]]
    assert f"`{newer[:7]}`" in bot["body"], "新しい run の候補が残る"
    assert f"`{head[:7]}`" not in bot["body"]
    async with async_sessionmaker(engine)() as s:
        events = await RunEventRepository(s).list_for_run(old_run)
    [ev] = [e for e in events if e.type == "spec_bot"]
    assert ev.payload["status"] == "stale"


async def test_spec_pr_bot_clears_its_comment_when_the_spec_change_is_reverted(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    repo = f"e2e/spec-bot-revert-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    llm = ScriptedJsonLlm(_SPEC_PROPOSAL)
    svc, host, base, head = _spec_bot_service(engine, tmp_path, repo, llm)
    root = tmp_path / "repo"
    git(root, "checkout", "-q", base, "--", "specs")
    git(root, "commit", "-qam", "revert spec")
    reverted = git(root, "rev-parse", "HEAD")
    project_id = await project_id_of(engine, repo)
    for sha in (head, reverted):
        await svc.create_run(
            project_id=project_id, head_sha=sha, base_sha=base, pr_number=11, trigger="test"
        )
    [bot] = [c for c in host.comments if SPEC_BOT_MARKER in c["body"]]
    assert "変更はありません" in bot["body"], "古い候補を残さない"
    assert len(llm.prompts) == 1
