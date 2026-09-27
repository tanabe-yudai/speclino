"""ReviewRunService の E2E（Postgres + ローカル git + インプロセスキュー。GitHub なし）。

`SPECLINO_DATABASE_URL` が未設定なら skip。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.db.repositories import (
    ApprovalRepository,
    FindingRepository,
    ProjectRepository,
    ReviewRunRepository,
    RunEventRepository,
)
from speclino.reporter.check_report import COMMENT_MARKER
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.spec import load_spec_ir
from speclino.spec.spec_validator import rule_content_hash


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
    import json

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
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo_name: tmp_path / "repo"})
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
        repo_full_name=repo, head_sha=head, base_sha=base, pr_number=1, trigger="pull_request"
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
        repo_full_name=repo, head_sha=head2, base_sha=base, pr_number=2, trigger="pull_request"
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
        repo_full_name=repo, head_sha=head2, base_sha=base, pr_number=2, trigger="rerun"
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
        repo_full_name=repo, head_sha=head, base_sha=base, pr_number=3, trigger="pull_request"
    )
    new = await svc.create_run(
        repo_full_name=repo, head_sha=head2, base_sha=base, pr_number=3, trigger="pull_request"
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

    design の「乗っ取られた場合でも最悪の結果は証拠不足 → 未検証 → ゲート失敗」を、
    エージェントの実行障害にも適用する。本番では Vertex AI の 429 で run 全体が
    `failed` になり、判定結果が一切表示されなかった。
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr("speclino.services.review_run_service.investigate", _explode)

    repo = f"e2e/agent-fail-{uuid.uuid4().hex[:8]}"
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

    host = LocalCodeHost(repos={repo: root})
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
        repo_full_name=repo, head_sha=head3, base_sha=base, pr_number=1, trigger="test"
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
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"})
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
            repo_full_name=repo, head_sha=head, base_sha=base, pr_number=5, trigger="test"
        )
    gate_comments = [c for c in host.comments if COMMENT_MARKER in c["body"]]
    assert len(gate_comments) == 1, [c["body"][:60] for c in host.comments]
    assert "SpecLino / gate" in gate_comments[0]["body"]


async def test_child_run_rejects_a_parent_from_another_project(
    service: tuple[ReviewRunService, LocalCodeHost, str, str, str, str],
) -> None:
    """子 run の親は同じ project の run に限る（別リポジトリの回答を取り込ませない。S52）。"""
    svc, host, repo, base, head, _ = service
    parent = await svc.create_run(
        repo_full_name=repo, head_sha=head, base_sha=base, pr_number=1, trigger="pull_request"
    )
    other = f"{repo}-other"
    host.repos[other] = host.repos[repo]
    with pytest.raises(ValueError, match="の run ではない"):
        await svc.create_run(
            repo_full_name=other,
            head_sha=head,
            base_sha=base,
            pr_number=1,
            trigger="comment",
            parent_run_id=parent,
        )
