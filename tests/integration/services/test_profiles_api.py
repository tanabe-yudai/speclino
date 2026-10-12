"""プロフィール（表示名・メール）の API と、uid の表示用の名前の統合試験。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。dev 認証では
`X-Dev-Email` / `X-Dev-Email-Verified` / `X-Dev-Name` で ID トークンのクレームを与える。
uid は後片付けのため e2e- で始める。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from speclino.services.profiles import display_names
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


def _uid(name: str) -> str:
    return f"e2e-{name}-{uuid.uuid4().hex[:8]}"


def _user(
    uid: str, email: str | None = None, verified: bool = True, name: str | None = None
) -> dict[str, str]:
    headers = {"X-Dev-User": uid}
    if email is not None:
        headers["X-Dev-Email"] = email
        headers["X-Dev-Email-Verified"] = "true" if verified else "false"
    if name is not None:
        headers["X-Dev-Name"] = name
    return headers


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all")
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


async def _workspace(engine: AsyncEngine, members: dict[str, str]) -> str:
    slug = f"e2e-prof-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}-repo", members, team=slug)
    return slug


async def _members(client: httpx.AsyncClient, slug: str, uid: str) -> dict[str, dict[str, Any]]:
    res = await client.get(f"/api/workspaces/{slug}/members", headers=_user(uid))
    assert res.status_code == 200, res.text
    return {m["uid"]: m for m in res.json()}


async def test_token_name_is_only_the_initial_display_name(client: httpx.AsyncClient) -> None:
    uid = _uid("alice")
    first = await client.get("/api/me", headers=_user(uid, "a@example.com", name="Alice"))
    assert first.status_code == 200, first.text
    assert first.json()["display_name"] == "Alice"

    res = await client.patch(
        "/api/me/profile", json={"display_name": "  Ali  "}, headers=_user(uid)
    )
    assert res.status_code == 200, res.text
    assert res.json()["display_name"] == "Ali"

    # 次のサインインでトークンの name が変わっても、本人の設定を上書きしない。メールは追従する
    again = await client.get("/api/me", headers=_user(uid, "a2@example.com", name="Alice B"))
    assert again.json()["display_name"] == "Ali"
    assert again.json()["email"] == "a2@example.com"

    # 消せば、以後のサインインでもトークンの name で埋め直さない
    cleared = await client.patch("/api/me/profile", json={"display_name": None}, headers=_user(uid))
    assert cleared.json()["display_name"] is None
    after = await client.get("/api/me", headers=_user(uid, "a2@example.com", name="Alice"))
    assert after.json()["display_name"] is None


async def test_unusable_token_name_is_not_stored(client: httpx.AsyncClient) -> None:
    uid = _uid("long")
    res = await client.get("/api/me", headers=_user(uid, name="x" * 80))
    assert res.json()["display_name"] is None


@pytest.mark.parametrize("bad", ["", "   ", "x" * 51, "a\nb", "a\u202eb"])
async def test_invalid_display_name_is_rejected(client: httpx.AsyncClient, bad: str) -> None:
    uid = _uid("bad")
    res = await client.patch("/api/me/profile", json={"display_name": bad}, headers=_user(uid))
    assert res.status_code == 422
    assert "error" in res.json()


async def test_profile_update_before_first_me_creates_the_row(client: httpx.AsyncClient) -> None:
    uid = _uid("early")
    res = await client.patch(
        "/api/me/profile",
        json={"display_name": "Early"},
        headers=_user(uid, "e@example.com", verified=False),
    )
    assert res.json() == {
        "uid": uid,
        "display_name": "Early",
        "email": "e@example.com",
        "email_verified": False,
    }


async def test_members_list_shows_display_name_and_email(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    owner, named, unverified, silent, invited = (
        _uid(n) for n in ("owner", "named", "unverified", "silent", "invited")
    )
    slug = await _workspace(
        engine,
        {
            owner: "owner",
            named: "member",
            unverified: "member",
            silent: "member",
            invited: "member",
        },
    )
    async with engine.begin() as conn:
        await conn.execute(
            sql_text("UPDATE workspace_members SET email = 'inv@example.com' WHERE uid = :u"),
            {"u": invited},
        )
    await client.get("/api/me", headers=_user(named, "n@example.com", name="Named"))
    await client.get("/api/me", headers=_user(unverified, "u@example.com", verified=False))

    members = await _members(client, slug, owner)
    assert members[named]["display_name"] == "Named"
    assert members[named]["label"] == "Named"
    assert (members[named]["email"], members[named]["email_verified"]) == ("n@example.com", True)
    assert members[unverified]["label"] == "u@example.com"
    assert members[unverified]["email_verified"] is False
    # 招待の受諾で入ったメール（確認済み）。プロフィールが無くても出す
    assert (members[invited]["email"], members[invited]["email_verified"]) == (
        "inv@example.com",
        True,
    )
    # 何も無ければ uid の短縮
    assert members[silent]["label"] == f"{silent[:8]}…"
    assert members[silent]["email"] is None and members[silent]["display_name"] is None


async def test_names_of_people_in_other_workspaces_are_not_resolved(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    me, stranger = _uid("me"), _uid("stranger")
    mine = await _workspace(engine, {me: "owner"})
    await _workspace(engine, {stranger: "owner"})
    await client.get("/api/me", headers=_user(stranger, "s@example.com", name="Stranger"))
    await client.get("/api/me", headers=_user(me, "me@example.com", name="Me"))

    # 一覧には同じワークスペースのメンバーしか出ない
    assert set(await _members(client, mine, me)) == {me}

    factory = async_sessionmaker(engine)
    async with factory() as s:
        labels = await display_names(s, workspace=mine, uids=[me, stranger, "github:12345", ""])
    assert labels == {me: "Me", stranger: f"{stranger[:8]}…", "github:12345": "github:12345"}


async def test_me_is_returned_even_if_the_profile_write_fails(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("speclino.services.profiles.record_sign_in", broken)
    uid = _uid("nodb")
    res = await client.get("/api/me", headers=_user(uid, "x@example.com", name="X"))
    assert res.status_code == 200, res.text
    assert res.json()["uid"] == uid and res.json()["display_name"] is None
    assert res.json()["workspaces"] == []
