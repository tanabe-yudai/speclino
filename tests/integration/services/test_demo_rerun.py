"""デモ再実行（S39）: ボタン相当の API で run が作り直され、日次上限で拒否される。"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, time
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.repositories import ReviewRunRepository
from speclino.services.demo_rerun import TRIGGER_PREFIX
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


async def test_rerun_creates_new_run_and_enforces_daily_limit(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    repo = f"e2e/rerun-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo)
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    # 日次カウンタは DB 全体で共有なので、既に残っている本日の再実行数に相対して上限を決める
    async with factory() as session:
        already = await ReviewRunRepository(session).count_since(
            TRIGGER_PREFIX, datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
        )
    limit = already + 2
    settings = Settings(
        env="local", role="all", code_host_kind="local", demo_rerun_limit_per_day=limit
    )
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        first = await c.post(
            "/api/review-runs",
            json={"repo_full_name": repo, "head_sha": head, "base_sha": base, "pr_number": 9},
        )
        original = first.json()["run_id"]
        # 1 回目・2 回目は許可され、新しい run が完了して同じ判定になる
        r1 = await c.post(f"/api/review-runs/{original}/rerun")
        assert r1.status_code == 202, r1.text
        body = r1.json()
        assert body["used_today"] == already + 1 and body["remaining"] == 1 and body["cost_ok"]
        detail = (await c.get(f"/api/review-runs/{body['run_id']}")).json()
        assert detail["trigger"] == "demo_rerun:dev" and detail["exec_state"] == "completed"
        assert detail["gate_status"] == "fail"
        # 再実行で古い run は superseded（同じ PR の最新だけが公開される）
        old = (await c.get(f"/api/review-runs/{original}")).json()
        assert old["exec_state"] in ("completed", "superseded")
        r2 = await c.post(f"/api/review-runs/{original}/rerun")
        assert r2.status_code == 202 and r2.json()["remaining"] == 0
        # 3 回目は上限で拒否（理由つき、ErrorResponse 形式）
        r3 = await c.post(f"/api/review-runs/{original}/rerun")
        assert r3.status_code == 429
        assert r3.json()["error"]["code"] == "rerun_limit"
        assert f"上限 {limit} 回" in r3.json()["error"]["message"]
        missing = await c.post(f"/api/review-runs/{uuid.uuid4()}/rerun")
        assert missing.status_code == 404
