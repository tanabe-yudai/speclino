"""ワークスペースを自分で作る API の統合試験。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
slug と uid は後片付けのため e2e- で始める。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services import knowledge_registry
from speclino.services.knowledge_presets import PRESET_ACTOR, preset_skills
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.services.workspace_create import MAX_OWNED_WORKSPACES, owned_limit
from speclino.settings import Settings

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


def _uid(name: str) -> str:
    return f"e2e-{name}-{uuid.uuid4().hex[:8]}"


def _slug() -> str:
    return f"e2e-new-{uuid.uuid4().hex[:8]}"


def _user(uid: str) -> dict[str, str]:
    return {"X-Dev-User": uid}


RAISED_UID = _uid("raised")


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", workspace_limit_raised_uids=f"other,{RAISED_UID}")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    host = LocalCodeHost(repos={})
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_creator_becomes_owner_and_sees_it_in_me(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    uid, slug = _uid("maker"), _slug()
    res = await client.post(
        "/api/workspaces", json={"slug": slug, "name": "  試用  "}, headers=_user(uid)
    )
    assert res.status_code == 201, res.text
    assert res.json() == {"slug": slug, "name": "試用", "role": "owner"}

    me = await client.get("/api/me", headers=_user(uid))
    assert {"slug": slug, "name": "試用", "role": "owner"} in me.json()["workspaces"]
    async with engine.connect() as conn:
        actor = (
            await conn.execute(
                sql_text(
                    "SELECT actor FROM audit_events WHERE action = 'workspace.create' "
                    "AND target = :slug"
                ),
                {"slug": slug},
            )
        ).scalar_one()
    assert actor == uid


async def test_existing_slug_is_not_taken_over(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    # メンバーのいない teams 行（規約だけが作った行など）も他人に引き取らせない
    slug = _slug()
    async with engine.begin() as conn:
        await conn.execute(
            sql_text("INSERT INTO teams (team, approvers) VALUES (:slug, '[]'::jsonb)"),
            {"slug": slug},
        )
    uid = _uid("taker")
    res = await client.post("/api/workspaces", json={"slug": slug}, headers=_user(uid))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "workspace_exists"
    me = await client.get("/api/me", headers=_user(uid))
    assert me.json()["workspaces"] == []


@pytest.mark.parametrize(
    "bad", ["a", "Upper", "-lead", "trail-", "two--hyphens", "with_underscore", "x" * 41, "日本"]
)
async def test_invalid_slug_is_rejected(client: httpx.AsyncClient, bad: str) -> None:
    res = await client.post("/api/workspaces", json={"slug": bad}, headers=_user(_uid("bad")))
    assert res.status_code == 422
    assert "error" in res.json()


async def test_invalid_name_is_rejected(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/workspaces", json={"slug": _slug(), "name": "a\nb"}, headers=_user(_uid("name"))
    )
    assert res.status_code == 422


async def test_owned_workspaces_are_limited(client: httpx.AsyncClient) -> None:
    uid = _uid("many")
    for _ in range(MAX_OWNED_WORKSPACES):
        res = await client.post("/api/workspaces", json={"slug": _slug()}, headers=_user(uid))
        assert res.status_code == 201, res.text
    over = await client.post("/api/workspaces", json={"slug": _slug()}, headers=_user(uid))
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "workspace_limit"


async def test_concurrent_creation_does_not_exceed_the_limit(client: httpx.AsyncClient) -> None:
    uid = _uid("race")
    results = await asyncio.gather(
        *(
            client.post("/api/workspaces", json={"slug": _slug()}, headers=_user(uid))
            for _ in range(MAX_OWNED_WORKSPACES + 3)
        )
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == MAX_OWNED_WORKSPACES, codes
    assert codes.count(409) == 3, codes


async def test_raised_uid_can_own_more_than_the_limit(client: httpx.AsyncClient) -> None:
    for _ in range(MAX_OWNED_WORKSPACES + 1):
        res = await client.post(
            "/api/workspaces", json={"slug": _slug()}, headers=_user(RAISED_UID)
        )
        assert res.status_code == 201, res.text


def test_owned_limit_reads_comma_separated_uids() -> None:
    assert owned_limit("a", " a , b ") > MAX_OWNED_WORKSPACES
    assert owned_limit("c", " a , b ") == MAX_OWNED_WORKSPACES
    assert owned_limit("a", "") == MAX_OWNED_WORKSPACES


async def test_new_workspace_starts_with_preset_skills(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = _slug()
    res = await client.post("/api/workspaces", json={"slug": slug}, headers=_user(_uid("preset")))
    assert res.status_code == 201, res.text

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        bundle = await knowledge_registry.current_bundle(session, slug)
    assert bundle is not None and bundle.generation == 1
    assert knowledge_registry.verify_bundle(bundle)
    presets = preset_skills()
    assert {(m.kind, m.name) for m in bundle.members} == {("skill", n) for n in presets}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sql_text("SELECT actor, action FROM audit_events WHERE target LIKE :t"),
                {"t": f"{slug}/%"},
            )
        ).all()
    assert {(r.actor, r.action) for r in rows} == {
        (PRESET_ACTOR, "knowledge.draft"),
        (PRESET_ACTOR, "knowledge.approve"),
    }
