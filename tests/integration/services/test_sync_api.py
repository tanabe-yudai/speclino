"""S68: sync トークンと `GET /api/sync/bundle`（api/routes/sync.py）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.domain.knowledge import Bundle, KnowledgeLock
from speclino.services import bundle_signing
from speclino.services import knowledge_sync as ks
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

OWNER = {"X-Dev-User": "owner1"}
APPROVER = {"X-Dev-User": "appr1"}
MEMBER = {"X-Dev-User": "member1"}
STRANGER = {"X-Dev-User": "owner9"}


@dataclass
class Ctx:
    c: httpx.AsyncClient
    engine: AsyncEngine
    slug: str
    repo: str
    project_id: str
    other_repo: str
    other_project_id: str


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-sy-{uuid.uuid4().hex[:8]}"
    other = f"e2e-syo-{uuid.uuid4().hex[:8]}"
    members = {"owner1": "owner", "appr1": "approver", "member1": "member"}
    await register_repo(engine, f"e2e/{slug}", members, slug)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield Ctx(
            c,
            engine,
            slug,
            f"e2e/{slug}",
            str(await project_id_of(engine, f"e2e/{slug}")),
            f"e2e/{other}",
            str(await project_id_of(engine, f"e2e/{other}")),
        )


async def _issue(
    x: Ctx, who: dict[str, str] = OWNER, project_id: str | None = None
) -> httpx.Response:
    return await x.c.post(
        f"/api/workspaces/{x.slug}/sync-tokens",
        json={"name": "laptop", "project_id": project_id or x.project_id},
        headers=who,
    )


async def _approved(x: Ctx, kind: str, name: str, content: dict[str, object]) -> None:
    res = await x.c.post(
        f"/api/workspaces/{x.slug}/artifacts",
        json={"kind": kind, "name": name, "content": content},
        headers=MEMBER,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    ok = await x.c.post(
        f"/api/workspaces/{x.slug}/artifacts/{body['artifact_id']}/approve",
        json={"expected_revision": body["revision"], "expected_hash": body["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text


async def _seed(x: Ctx) -> None:
    await _approved(
        x,
        "instructions",
        "agents",
        {
            "kind": "instructions",
            "target": "agents_md",
            "body": "共通",
            "scope": {"workspace": True},
        },
    )
    await _approved(
        x,
        "skill",
        "review",
        {
            "kind": "skill",
            "skill_md": "---\nname: review\ndescription: d\n---\n手順\n",
            "files": {"ref/check.md": "チェック"},
            "scope": {"projects": [x.project_id]},
        },
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_sync_token_permissions(ctx: Ctx) -> None:
    x = ctx
    denied = await _issue(x, MEMBER)
    assert denied.status_code == 403
    assert (await _issue(x, STRANGER)).status_code == 404
    # 別のワークスペースのプロジェクトには発行できない
    assert (await _issue(x, OWNER, x.other_project_id)).status_code == 404
    issued = await _issue(x, APPROVER)
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    assert token.startswith("slsync_")
    assert issued.json()["repo_full_name"] == x.repo

    listed = await x.c.get(f"/api/workspaces/{x.slug}/sync-tokens", headers=MEMBER)
    assert listed.status_code == 200
    assert [t["name"] for t in listed.json()] == ["laptop"]
    assert token not in listed.text
    assert (
        await x.c.get(f"/api/workspaces/{x.slug}/sync-tokens", headers=STRANGER)
    ).status_code == 404
    async with x.engine.connect() as conn:
        stored = (
            await conn.execute(
                text("SELECT token_hash FROM sync_tokens WHERE team = :t"), {"t": x.slug}
            )
        ).scalar_one()
    assert token not in stored and len(stored) == 64

    tid = issued.json()["id"]
    assert (
        await x.c.delete(f"/api/workspaces/{x.slug}/sync-tokens/{tid}", headers=MEMBER)
    ).status_code == 403
    assert (
        await x.c.delete(f"/api/workspaces/{x.slug}/sync-tokens/{tid}", headers=OWNER)
    ).status_code == 204
    gone = await x.c.get(
        "/api/sync/bundle", params={"repo_id": repo_id_of(x.repo)}, headers=_bearer(token)
    )
    assert gone.status_code == 401


async def test_sync_bundle_writes_and_verifies(ctx: Ctx, tmp_path: Path) -> None:
    x = ctx
    await _seed(x)
    token = (await _issue(x)).json()["token"]
    res = await x.c.get(
        "/api/sync/bundle", params={"repo_id": repo_id_of(x.repo)}, headers=_bearer(token)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert token not in res.text
    files: dict[str, str] = body["files"]
    assert files["AGENTS.md"] == "共通\n"
    assert files[".claude/skills/review/ref/check.md"] == "チェック"
    assert ".agents/skills/review/SKILL.md" in files
    lock = KnowledgeLock.model_validate(body["lock"])
    bundle = Bundle.model_validate(body["bundle"])
    assert bundle_signing.verify(ks.lock_payload(lock), lock.signature)
    assert lock.bundle_generation == bundle.generation == 2
    assert lock.github_repo_id == repo_id_of(x.repo)
    assert {m.name for m in lock.members} == {"agents", "review"}

    repo = tmp_path / "checkout"
    repo.mkdir()
    ks.write_bundle(repo, files, lock)
    lock_json = (repo / ks.LOCK_PATH).read_text()
    assert ks.check_lock(repo, lock_json, current=bundle).verdict == "VERIFIED_IN_SCOPE"

    async with x.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT channel, token_id, bundle_generation, project_id, artifact_ids "
                    "FROM knowledge_fetches WHERE workspace = :w"
                ),
                {"w": x.slug},
            )
        ).one()
    assert row.channel == "sync" and row.bundle_generation == 2
    assert str(row.project_id) == x.project_id
    assert len(row.artifact_ids) == 2


async def test_sync_bundle_other_repo_is_404(ctx: Ctx) -> None:
    x = ctx
    token = (await _issue(x)).json()["token"]
    other = await x.c.get(
        "/api/sync/bundle", params={"repo_id": repo_id_of(x.other_repo)}, headers=_bearer(token)
    )
    assert other.status_code == 404
    missing = await x.c.get("/api/sync/bundle", params={"repo_id": 1}, headers=_bearer(token))
    assert missing.status_code == 404
    no_token = await x.c.get("/api/sync/bundle", params={"repo_id": repo_id_of(x.repo)})
    assert no_token.status_code == 401
    mcp = await x.c.post(f"/api/workspaces/{x.slug}/mcp-tokens", json={"name": "m"}, headers=OWNER)
    wrong_kind = await x.c.get(
        "/api/sync/bundle",
        params={"repo_id": repo_id_of(x.repo)},
        headers=_bearer(mcp.json()["token"]),
    )
    assert wrong_kind.status_code == 401


async def test_sync_bundle_without_knowledge_is_empty(ctx: Ctx) -> None:
    x = ctx
    token = (await _issue(x)).json()["token"]
    res = await x.c.get(
        "/api/sync/bundle", params={"repo_id": repo_id_of(x.repo)}, headers=_bearer(token)
    )
    assert res.status_code == 200, res.text
    assert res.json()["files"] == {}
    assert res.json()["lock"]["bundle_generation"] == 1
