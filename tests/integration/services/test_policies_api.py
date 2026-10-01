"""`api/routes/policies.py` の統合試験（S43、design/2026-09-30-ui-redesign.md 6・8 章）。

fixture は test_projects_api.py と同じ作り（メンバーは owner1 / member1）。

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
from speclino.api.deps import AppState, make_team_policy_resolver
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


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-pol-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"owner1": "owner", "member1": "member"}, team=slug)
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    service = ReviewRunService(
        factory,
        host,
        queue,
        blobs,
        ReviewRunConfig(
            agent_enabled=False, resolve_team_policy=make_team_policy_resolver(factory)
        ),
    )
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


MEMBER = {"X-Dev-User": "member1"}
OWNER = {"X-Dev-User": "owner1"}
RULE = {
    "id": "DOC-001",
    "version": "1.0.0",
    "level": "advisory",
    "title": "設計判断を残す",
    "text": "本文",
    "applies_to": {"paths": ["docs/**"]},
    "verifier": None,
    "on_missing_input": "unknown",
}


async def test_non_member_gets_404(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [RULE]}
    bodies = {
        "/draft": {"package": pkg, "note": "", "expected_revision": None, "expected_hash": None},
        "/draft/approve": {"expected_revision": 1, "expected_hash": "sha256:x"},
    }
    for method, path in (
        ("GET", ""),
        ("GET", "/revisions"),
        ("GET", "/audit"),
        ("PUT", "/draft"),
        ("POST", "/draft/approve"),
    ):
        res = await c.request(
            method,
            f"/api/workspaces/{slug}/policies{path}",
            json=bodies.get(path),
            headers={"X-Dev-User": "stranger"},
        )
        assert res.status_code == 404, (method, path, res.text)


async def test_empty_workspace_has_no_policies(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["active"] is None and body["draft"] is None and body["can_approve"] is False
    assert "spec_tests" in body["verifiers"]


async def test_member_drafts_owner_approves(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [RULE]}
    saved = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg, "note": "追加", "expected_revision": None, "expected_hash": None},
        headers=MEMBER,
    )
    assert saved.status_code == 200, saved.text
    rev = saved.json()["revision"]
    h = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()["draft_hash"]
    stale_put = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg, "note": "", "expected_revision": None, "expected_hash": None},
        headers=OWNER,
    )
    assert stale_put.status_code == 409, stale_put.text
    denied = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": h},
        headers=MEMBER,
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "not_an_approver"
    stale = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": "sha256:stale"},
        headers=OWNER,
    )
    assert stale.status_code == 409
    ok = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": h},
        headers=OWNER,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=OWNER)).json()
    assert (
        body["active"]["revision"] == rev and body["draft"] is None and body["can_approve"] is True
    )
    audit = (await c.get(f"/api/workspaces/{slug}/policies/audit", headers=MEMBER)).json()
    assert [(a["action"], a["decision"]) for a in audit][:3] == [
        ("policy.approve", "approved"),
        ("policy.approve", "denied"),
        ("policy.draft", "created"),
    ]
    [summary] = (await c.get(f"/api/workspaces/{slug}/policies/revisions", headers=MEMBER)).json()
    assert summary["status"] == "active" and summary["rule_count"] == 1


async def test_invalid_package_is_422(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    bad_id = {**RULE, "id": "A:B"}
    unknown = {**RULE, "verifier": {"id": "nope", "version": "1"}}
    for rule in (bad_id, unknown):
        pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [rule]}
        res = await c.put(
            f"/api/workspaces/{slug}/policies/draft",
            json={"package": pkg, "note": "", "expected_revision": None, "expected_hash": None},
            headers=MEMBER,
        )
        assert res.status_code == 422, res.text
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["draft"] is None


async def test_results_come_from_the_latest_runs(ctx: Ctx) -> None:
    """承認したチーム規約が次の検査で使われ、その結果が規約ごとに集計される。

    適用範囲に当てはまるファイルが無い検証器つきのルールは「未検証」を必ず 1 件出す
    （on_missing_input=unknown）。題材のリポジトリの中身に依存しない。
    """
    c, slug, repo, base, head, *_ = ctx
    rule = {
        **RULE,
        "id": "TEAM-001",
        "applies_to": {"paths": ["no-such-dir/**"]},
        "verifier": {"id": "spec_tests", "version": "1.0.0"},
    }
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [rule]}
    saved = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg, "note": "", "expected_revision": None, "expected_hash": None},
        headers=OWNER,
    )
    assert saved.status_code == 200, saved.text
    h = (await c.get(f"/api/workspaces/{slug}/policies", headers=OWNER)).json()["draft_hash"]
    approved = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": saved.json()["revision"], "expected_hash": h},
        headers=OWNER,
    )
    assert approved.status_code == 200, approved.text
    run = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers=MEMBER,
    )
    assert run.status_code == 202, run.text
    detail = (await c.get(f"/api/review-runs/{run.json()['run_id']}", headers=MEMBER)).json()
    assert detail["manifest"]["policy_revision"] == saved.json()["revision"]
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["results"]["TEAM-001"] == {
        "status": "UNKNOWN",
        "projects": 1,
        "failing_projects": 1,
    }
    # 規約を変えて承認したら、古い改訂での検査の結果は出さない（次の検査までは「結果なし」）
    rule2 = {**rule, "title": "変えた題"}
    pkg2 = {**pkg, "rules": [rule2]}
    saved2 = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg2, "note": "", "expected_revision": None, "expected_hash": None},
        headers=OWNER,
    )
    assert saved2.status_code == 200, saved2.text
    h2 = (await c.get(f"/api/workspaces/{slug}/policies", headers=OWNER)).json()["draft_hash"]
    ok2 = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": saved2.json()["revision"], "expected_hash": h2},
        headers=OWNER,
    )
    assert ok2.status_code == 200, ok2.text
    after = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert after["results"] == {}
