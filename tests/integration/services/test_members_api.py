"""招待とメンバー管理の API の統合試験（S56）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。dev 認証では
`X-Dev-Email` / `X-Dev-Email-Verified` でメールとその確認状態を与える。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.models import AuditEvent
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]

OWNER = {"X-Dev-User": "dev"}


def _user(uid: str, email: str | None = None, verified: bool = True) -> dict[str, str]:
    headers = {"X-Dev-User": uid}
    if email is not None:
        headers["X-Dev-Email"] = email
        headers["X-Dev-Email-Verified"] = "true" if verified else "false"
    return headers


def _slug() -> str:
    return f"e2e-mem-{uuid.uuid4().hex[:8]}"


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
    slug = _slug()
    await register_repo(engine, f"e2e/{slug}-repo", members, team=slug)
    return slug


async def _audit(engine: AsyncEngine, action: str, target: str) -> list[AuditEvent]:
    async with async_sessionmaker(engine)() as s:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.action == action, AuditEvent.target == target)
            .order_by(AuditEvent.ts)
        )
        return list((await s.execute(stmt)).scalars())


async def _invite(
    c: httpx.AsyncClient, slug: str, email: str, role: str = "member"
) -> dict[str, str]:
    res = await c.post(
        f"/api/workspaces/{slug}/invitations", json={"email": email, "role": role}, headers=OWNER
    )
    assert res.status_code == 201, res.text
    body: dict[str, str] = res.json()
    return body


async def _members(c: httpx.AsyncClient, slug: str) -> dict[str, dict[str, str]]:
    res = await c.get(f"/api/workspaces/{slug}/members", headers=OWNER)
    assert res.status_code == 200, res.text
    return {m["uid"]: m for m in res.json()}


# ------------------------------------------------------------------ 招待


async def test_owner_invites_and_invitee_accepts(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner"})
    issued = await _invite(client, slug, "  Invitee@Example.com ", role="approver")
    assert issued["token"].startswith("slinv_")
    assert issued["email"] == "invitee@example.com" and issued["role"] == "approver"
    expires = datetime.fromisoformat(issued["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)

    # 一覧に平文は出ない
    listing = await client.get(f"/api/workspaces/{slug}/invitations", headers=OWNER)
    assert listing.status_code == 200
    [entry] = listing.json()
    assert entry["id"] == issued["id"] and "token" not in entry

    invitee = _user(f"uid-{uuid.uuid4().hex[:6]}", "INVITEE@example.com")
    preview = await client.post(
        "/api/invitations/preview", json={"token": issued["token"]}, headers=invitee
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["slug"] == slug and preview.json()["role"] == "approver"

    accepted = await client.post(
        "/api/invitations/accept", json={"token": issued["token"]}, headers=invitee
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"slug": slug, "role": "approver", "status": "joined"}

    members = await _members(client, slug)
    assert members[invitee["X-Dev-User"]]["role"] == "approver"
    assert members[invitee["X-Dev-User"]]["email"] == "invitee@example.com"

    # 1 回限り
    again = await client.post(
        "/api/invitations/accept", json={"token": issued["token"]}, headers=invitee
    )
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "invitation_not_found"

    issue_events = await _audit(engine, "invitation.issue", slug)
    accept_events = await _audit(engine, "invitation.accept", slug)
    assert len(issue_events) == 1 and issue_events[0].actor == "dev"
    assert "token" not in issue_events[0].detail
    assert len(accept_events) == 1 and accept_events[0].decision == "accepted"
    assert accept_events[0].actor == invitee["X-Dev-User"]


async def test_only_owner_can_invite(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug = await _workspace(engine, {"dev": "owner", "appr": "approver", "mem": "member"})
    path = f"/api/workspaces/{slug}/invitations"
    for uid in ("appr", "mem"):
        res = await client.post(
            path, json={"email": "x@example.com", "role": "member"}, headers=_user(uid)
        )
        assert res.status_code == 403, res.text
        assert res.json()["error"]["code"] == "not_an_owner"
        assert (await client.get(path, headers=_user(uid))).status_code == 403
    denied = await _audit(engine, "invitation.issue", slug)
    assert [e.decision for e in denied] == ["denied", "denied"]
    assert {e.actor for e in denied} == {"appr", "mem"}

    outsider = await client.post(
        path, json={"email": "x@example.com", "role": "member"}, headers=_user("nobody")
    )
    assert outsider.status_code == 404


async def test_invalid_email_is_rejected(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug = await _workspace(engine, {"dev": "owner"})
    res = await client.post(
        f"/api/workspaces/{slug}/invitations",
        json={"email": "not-an-email", "role": "member"},
        headers=OWNER,
    )
    assert res.status_code == 422


async def test_accept_requires_matching_verified_email(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner"})
    issued = await _invite(client, slug, "right@example.com")
    body = {"token": issued["token"]}

    wrong = await client.post(
        "/api/invitations/accept", json=body, headers=_user("eve", "eve@example.com")
    )
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invitation_email_mismatch"

    unverified = await client.post(
        "/api/invitations/accept",
        json=body,
        headers=_user("eve2", "right@example.com", verified=False),
    )
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "email_not_verified"

    no_email = await client.post("/api/invitations/accept", json=body, headers=_user("eve3"))
    assert no_email.status_code == 403

    members = await _members(client, slug)
    assert set(members) == {"dev"}, "拒否された人はメンバーにならない"
    denied = await _audit(engine, "invitation.accept", slug)
    assert [e.decision for e in denied] == ["denied"] * 3
    assert {e.detail["reason"] for e in denied} == {"email_mismatch", "email_not_verified"}

    # 拒否では消費されず、本人なら受諾できる
    ok = await client.post(
        "/api/invitations/accept", json=body, headers=_user("rightful", "right@example.com")
    )
    assert ok.status_code == 200, ok.text


async def test_expired_and_revoked_invitations_cannot_be_used(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner"})
    expired = await _invite(client, slug, "late@example.com")
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": expired["id"]},
        )
    late = _user("late", "late@example.com")
    res = await client.post(
        "/api/invitations/accept", json={"token": expired["token"]}, headers=late
    )
    assert res.status_code == 404
    preview = await client.post(
        "/api/invitations/preview", json={"token": expired["token"]}, headers=late
    )
    assert preview.status_code == 404

    revoked = await _invite(client, slug, "gone@example.com")
    path = f"/api/workspaces/{slug}/invitations/{revoked['id']}"
    assert (await client.delete(path, headers=OWNER)).status_code == 204
    assert (await client.delete(path, headers=OWNER)).status_code == 404
    res = await client.post(
        "/api/invitations/accept",
        json={"token": revoked["token"]},
        headers=_user("gone", "gone@example.com"),
    )
    assert res.status_code == 404
    assert len(await _audit(engine, "invitation.revoke", slug)) == 1

    unknown = await client.post(
        "/api/invitations/accept", json={"token": "slinv_nope"}, headers=late
    )
    assert unknown.status_code == 404


async def test_reinvite_replaces_pending_invitation(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """同じメールへの招待は 1 本だけ有効（古いリンクを生かしておかない）。"""
    slug = await _workspace(engine, {"dev": "owner"})
    first = await _invite(client, slug, "twice@example.com")
    second = await _invite(client, slug, "twice@example.com")
    who = _user("twice", "twice@example.com")
    old = await client.post("/api/invitations/accept", json={"token": first["token"]}, headers=who)
    assert old.status_code == 404
    new = await client.post("/api/invitations/accept", json={"token": second["token"]}, headers=who)
    assert new.status_code == 200


async def test_existing_member_keeps_role(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug = await _workspace(engine, {"dev": "owner", "bob": "approver"})
    issued = await _invite(client, slug, "bob@example.com", role="member")
    res = await client.post(
        "/api/invitations/accept",
        json={"token": issued["token"]},
        headers=_user("bob", "bob@example.com"),
    )
    assert res.status_code == 200
    assert res.json() == {"slug": slug, "role": "approver", "status": "already_member"}
    assert (await _members(client, slug))["bob"]["role"] == "approver"


async def test_revoke_by_non_owner_is_denied(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner", "mem": "member"})
    issued = await _invite(client, slug, "z@example.com")
    path = f"/api/workspaces/{slug}/invitations/{issued['id']}"
    assert (await client.delete(path, headers=_user("mem"))).status_code == 403
    assert (await client.delete(path, headers=_user("nobody"))).status_code == 404
    # 別ワークスペースの ID では取り消せない
    other = await _workspace(engine, {"dev": "owner"})
    cross = await client.delete(
        f"/api/workspaces/{other}/invitations/{issued['id']}", headers=OWNER
    )
    assert cross.status_code == 404


# ------------------------------------------------------------------ メンバーと role


async def test_members_are_listed_for_members_only(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner", "mem": "member"})
    res = await client.get(f"/api/workspaces/{slug}/members", headers=_user("mem"))
    assert res.status_code == 200
    assert {m["uid"]: m["role"] for m in res.json()} == {"dev": "owner", "mem": "member"}
    outsider = await client.get(f"/api/workspaces/{slug}/members", headers=_user("nobody"))
    assert outsider.status_code == 404


async def test_owner_changes_role_and_removes_member(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner", "mem": "member"})
    path = f"/api/workspaces/{slug}/members/mem"
    res = await client.patch(path, json={"role": "approver"}, headers=OWNER)
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "approver"
    assert (await _members(client, slug))["mem"]["role"] == "approver"

    [change] = await _audit(engine, "member.role_change", slug)
    assert change.actor == "dev" and change.decision == "changed"
    assert change.detail == {"uid": "mem", "before": "member", "after": "approver"}

    assert (await client.delete(path, headers=OWNER)).status_code == 204
    assert "mem" not in await _members(client, slug)
    [removal] = await _audit(engine, "member.remove", slug)
    assert removal.detail["uid"] == "mem" and removal.detail["role"] == "approver"

    # 居ない人は 404
    assert (await client.delete(path, headers=OWNER)).status_code == 404
    missing = await client.patch(path, json={"role": "member"}, headers=OWNER)
    assert missing.status_code == 404


async def test_non_owner_cannot_change_roles(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner", "appr": "approver", "mem": "member"})
    # approver が自分（や他人）を owner に上げられない
    up = await client.patch(
        f"/api/workspaces/{slug}/members/appr", json={"role": "owner"}, headers=_user("appr")
    )
    assert up.status_code == 403
    rm = await client.delete(f"/api/workspaces/{slug}/members/dev", headers=_user("mem"))
    assert rm.status_code == 403
    outsider = await client.patch(
        f"/api/workspaces/{slug}/members/mem", json={"role": "owner"}, headers=_user("nobody")
    )
    assert outsider.status_code == 404
    assert (await _members(client, slug)).keys() == {"dev", "appr", "mem"}
    assert (await _members(client, slug))["appr"]["role"] == "approver"
    changes = await _audit(engine, "member.role_change", slug)
    removals = await _audit(engine, "member.remove", slug)
    assert [e.decision for e in changes + removals] == ["denied", "denied"]


async def test_last_owner_cannot_be_demoted_or_removed(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = await _workspace(engine, {"dev": "owner", "mem": "member"})
    demote = await client.patch(
        f"/api/workspaces/{slug}/members/dev", json={"role": "member"}, headers=OWNER
    )
    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "last_owner"
    remove = await client.delete(f"/api/workspaces/{slug}/members/dev", headers=OWNER)
    assert remove.status_code == 409
    assert (await _members(client, slug))["dev"]["role"] == "owner"
    denied = await _audit(engine, "member.role_change", slug) + await _audit(
        engine, "member.remove", slug
    )
    assert {e.detail["reason"] for e in denied} == {"last_owner"}

    # owner が 2 人なら、片方を外せる
    await client.patch(f"/api/workspaces/{slug}/members/mem", json={"role": "owner"}, headers=OWNER)
    ok = await client.patch(
        f"/api/workspaces/{slug}/members/dev", json={"role": "approver"}, headers=OWNER
    )
    assert ok.status_code == 200
    assert (await _members(client, slug))["dev"]["role"] == "approver"


async def test_invalid_role_is_rejected(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug = await _workspace(engine, {"dev": "owner", "mem": "member"})
    res = await client.patch(
        f"/api/workspaces/{slug}/members/mem", json={"role": "admin"}, headers=OWNER
    )
    assert res.status_code == 422
