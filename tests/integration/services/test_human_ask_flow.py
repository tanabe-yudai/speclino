"""human.ask（FR 015）: 契約の無い外部呼出し → 質問 → 承認者の回答 → 子 run で再開（デモ 6）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.llm.scripted import ScriptedLlm, call, text
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.repositories import QuestionRepository, ReviewRunRepository
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import set_project_approvers
from tests.integration.services.test_api_flow import SECRET, comment_payload, pr_payload, signed

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]
ORDERS = Path(__file__).resolve().parents[3] / "examples" / "orders"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_repo(root: Path) -> tuple[str, str]:
    """base=正しい実装、head=在庫復元を契約の無い外部ライブラリ呼出しに置き換える。"""
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
    git(root, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    svc = app_dst / "service.py"
    src = svc.read_text()
    src = src.replace(
        "from examples.orders.app import audit, inventory\n",
        "from examples.orders.app import audit, inventory\nfrom warehouse_sdk import stock\n",
    ).replace("    inventory.restore(order.items)\n", "    stock.release(order.items)\n")
    svc.write_text(src)
    git(root, "commit", "-qam", "head: use warehouse_sdk.stock.release")
    return base, git(root, "rev-parse", "HEAD")


async def test_question_then_answer_resumes_child_run(engine: AsyncEngine, tmp_path: Path) -> None:
    ScriptedLlm.reset()
    repo = f"e2e/ask-{uuid.uuid4().hex[:8]}"
    base, head = make_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    # エージェントの台本: 契約を探し、候補を挙げて承認者に聞く
    llm = ScriptedLlm(
        steps=[
            [call("policy_check")],
            [call("contract_lookup", symbol="warehouse_sdk.stock.release")],
            [
                call(
                    "human_ask",
                    kind="binding",
                    requirement_id="R-CANCEL-04",
                    symbol="warehouse_sdk.stock.release",
                    question="warehouse_sdk.stock.release は在庫復元コマンドに対応しますか？",
                    options=["inventory_restore_command", "none"],
                )
            ],
            [text("承認者の回答待ち")],
        ]
    )
    settings = Settings(env="local", role="all", github_webhook_secret=SECRET)
    service = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=True, model=llm),
    )
    holder["svc"] = service
    state = AppState(
        settings, engine, factory, host, queue, FileSystemBlobStore(tmp_path / "blobs"), service
    )
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        raw, headers = signed(pr_payload(repo, head, base, 7), "pull_request")
        first = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
        run_id = uuid.UUID(first["run_id"])
        await set_project_approvers(engine, repo, ["approver"])
        async with factory() as s:
            run = await ReviewRunRepository(s).get(run_id)
            assert run is not None and run.exec_state == "awaiting_answer"
            qs = await QuestionRepository(s).list_for_run(run_id, "open")
            assert len(qs) == 1 and qs[0].options["options"] == [
                "inventory_restore_command",
                "none",
            ]
            short = str(qs[0].id)[:8]
        # 未解決（契約なし）なので R-CANCEL-04 は UNKNOWN、Check は action_required で回答待ち
        detail = (await c.get(f"/api/review-runs/{run_id}")).json()
        assert (
            next(f["verdict"] for f in detail["findings"] if f["requirement_id"] == "R-CANCEL-04")
            == "UNKNOWN"
        )
        assert (
            host.checks[1]["conclusion"] == "action_required"
            and "回答待ち" in host.checks[1]["title"]
        )
        assert host.comments and f"Q-{short}" in host.comments[-1]["body"]
        # 承認者でない回答は拒否
        raw, headers = signed(
            comment_payload(
                repo, 7, f"/speclino answer Q-{short} inventory_restore_command", "stranger"
            ),
            "issue_comment",
        )
        assert (await c.post("/webhooks/github", content=raw, headers=headers)).json()["commands"][
            0
        ]["accepted"] is False
        # 別リポジトリの PR から、その承認者がこの質問に回答しても受理しない（S52）
        other = f"{repo}-other"
        host.repos[other] = host.repos[repo]
        raw, headers = signed(pr_payload(other, head, base, 7), "pull_request")
        await c.post("/webhooks/github", content=raw, headers=headers)
        await set_project_approvers(engine, other, ["intruder"])
        raw, headers = signed(
            comment_payload(
                other, 7, f"/speclino answer Q-{short} inventory_restore_command", "intruder"
            ),
            "issue_comment",
        )
        cross = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
        assert cross["commands"][0]["accepted"] is False, cross
        async with factory() as s:
            assert len(await QuestionRepository(s).list_for_run(run_id, "open")) == 1
        # 承認者の回答 → 子 run（親の回答を bindings に適用）→ 通過
        ScriptedLlm.reset()
        raw, headers = signed(
            comment_payload(
                repo, 7, f"/speclino answer Q-{short} inventory_restore_command", "approver"
            ),
            "issue_comment",
        )
        res = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
        assert res["status"] == "queued", res
        child = (await c.get(f"/api/review-runs/{res['run_id']}")).json()
        assert child["exec_state"] == "completed" and child["gate_status"] == "pass", child[
            "findings"
        ]
        events = (await c.get(f"/api/review-runs/{res['run_id']}/events")).json()
        assert any(
            e["type"] == "human_answer" and e["payload"]["option"] == "inventory_restore_command"
            for e in events
        )
        async with factory() as s:
            child_run = await ReviewRunRepository(s).get(uuid.UUID(res["run_id"]))
            assert child_run is not None and child_run.parent_run_id == run_id
