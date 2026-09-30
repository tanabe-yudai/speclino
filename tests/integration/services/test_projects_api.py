"""`api/routes/projects.py` の統合試験（S45、design/2026-09-30-ui-redesign.md 5.2・8 章）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]

Ctx = tuple[httpx.AsyncClient, str, str, str, str, str, uuid.UUID, Path]
DEV = {"X-Dev-User": "dev"}


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-proj-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"dev": "member"}, team=slug)
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield (
            c,
            slug,
            repo,
            base,
            head,
            head2,
            await project_id_of(engine, repo),
            tmp_path / "blobs",
        )


async def _run(c: httpx.AsyncClient, repo: str, head: str, base: str) -> str:
    res = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers=DEV,
    )
    assert res.status_code == 202, res.text
    return str(res.json()["run_id"])


async def test_outsiders_get_404(ctx: Ctx, engine: AsyncEngine) -> None:
    c, slug, _, _, _, _, pid, _ = ctx
    stranger = {"X-Dev-User": "stranger"}
    for path in (
        f"/api/workspaces/{slug}/projects",
        f"/api/workspaces/{slug}/projects/{pid}/overview",
    ):
        res = await c.get(path, headers=stranger)
        assert res.status_code == 404 and res.json()["error"]["code"] == "workspace_not_found"
    # 自分のワークスペースの slug で、他のワークスペースの project を指しても 404
    other = f"e2e-other-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{other}", {"dev": "member"}, team=other)
    res = await c.get(f"/api/workspaces/{other}/projects/{pid}/overview", headers=DEV)
    assert res.status_code == 404


async def test_not_checked_before_any_run(ctx: Ctx) -> None:
    c, slug, repo, _, _, _, pid, _ = ctx
    [summary] = (await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)).json()
    assert (
        summary["repo_full_name"] == repo
        and summary["latest_run"] is None
        and summary["counts"] is None
    )
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "not_checked" and body["requirements"] == [] and body["runs"] == []


async def test_overview_after_a_violating_run(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, _ = ctx
    run_id = await _run(c, repo, head, base)
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "ok" and body["run"]["id"] == run_id
    req = {r["id"]: r for r in body["requirements"]}
    assert (
        req["R-CANCEL-04"]["verdict"] == "VIOLATED" and req["R-CANCEL-04"]["approval"] == "approved"
    )
    assert body["counts"]["VIOLATED"] >= 1
    assert [r["id"] for r in body["runs"]] == [run_id]
    assert all(p["rule_id"] and p["counts"] for p in body["policies"])
    [summary] = (await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)).json()
    assert summary["latest_run"]["id"] == run_id and summary["counts"]["VIOLATED"] >= 1


async def test_removed_requirement_is_stale(ctx: Ctx) -> None:
    c, slug, repo, base, _, head2, pid, _ = ctx
    await _run(c, repo, head2, base)  # head2: R-CANCEL-04 を IR から消したコミット
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    removed = [r for r in body["requirements"] if r["removed"]]
    assert [(r["id"], r["approval"]) for r in removed] == [("R-CANCEL-04", "stale")]


async def test_missing_artifacts_are_unavailable_not_500(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, blobs = ctx
    run_id = await _run(c, repo, head, base)
    for p in (blobs / "runs" / run_id).glob("spec-ir*.json"):
        p.unlink()
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert (
        body["status"] == "unavailable"
        and body["run"]["id"] == run_id
        and body["requirements"] == []
    )


async def test_corrupt_artifact_does_not_break_the_list(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, blobs = ctx
    run_id = await _run(c, repo, head, base)
    (blobs / "runs" / run_id / "spec-ir.json").write_text("{not json")
    res = await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)
    assert res.status_code == 200, res.text
    [summary] = res.json()
    assert summary["status"] == "unavailable" and summary["counts"] is None
    assert summary["latest_run"]["id"] == run_id
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "unavailable"


async def test_card_and_page_count_the_same(ctx: Ctx) -> None:
    c, slug, repo, base, _, head2, pid, _ = ctx
    await _run(c, repo, head2, base)  # 削除された要求（stale_approval）を含む run
    [summary] = (await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)).json()
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert summary["status"] == "ok"
    assert summary["counts"] == body["counts"] and summary["unanalyzed"] == body["unanalyzed"]


async def test_missing_gate_json_is_unavailable(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, blobs = ctx
    run_id = await _run(c, repo, head, base)
    (blobs / "runs" / run_id / "gate.json").unlink()
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "unavailable"
