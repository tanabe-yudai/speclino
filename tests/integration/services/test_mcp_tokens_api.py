"""`api/routes/mcp_tokens.py` の統合試験（S54、design/2026-09-27-workspaces.md 4・8.1 節）。

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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


def _slug() -> str:
    return f"e2e-mcp-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all")
    service = ReviewRunService(
        factory,
        LocalCodeHost(repos={}),
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=False),
    )
    holder["svc"] = service
    state = AppState(
        settings,
        engine,
        factory,
        LocalCodeHost(repos={}),
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        service,
    )
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def _audit_rows(
    factory: async_sessionmaker[AsyncSession], action: str, target: str
) -> list[AuditEvent]:
    async with factory() as session:
        stmt = select(AuditEvent).where(AuditEvent.action == action, AuditEvent.target == target)
        return list((await session.execute(stmt)).scalars())


async def test_owner_can_issue_list_and_revoke(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug = _slug()
    await register_repo(engine, f"e2e/{slug}-repo", {"dev": "owner"}, team=slug)
    owner = {"X-Dev-User": "dev"}
    path = f"/api/workspaces/{slug}/mcp-tokens"

    issued = await client.post(path, json={"name": "editor"}, headers=owner)
    assert issued.status_code == 201, issued.text
    body = issued.json()
    assert body["token"].startswith("slmcp_")
    assert body["name"] == "editor" and body["created_by"] == "dev"
    assert body["revoked_at"] is None and body["last_used_at"] is None
    token_id = body["id"]

    # 一覧には平文もハッシュも出ない
    listing = await client.get(path, headers=owner)
    assert listing.status_code == 200
    [entry] = listing.json()
    assert entry["id"] == token_id and "token" not in entry

    revoked = await client.delete(f"{path}/{token_id}", headers=owner)
    assert revoked.status_code == 204

    # 失効済みの再失効は 404
    again = await client.delete(f"{path}/{token_id}", headers=owner)
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "workspace_not_found"

    listing_after = await client.get(path, headers=owner)
    assert listing_after.json()[0]["revoked_at"] is not None

    # 監査に issue / revoke が残り、平文は含まれない
    factory = async_sessionmaker(engine, expire_on_commit=False)
    issue_events = await _audit_rows(factory, "mcp_token.issue", slug)
    revoke_events = await _audit_rows(factory, "mcp_token.revoke", slug)
    assert len(issue_events) == 1 and len(revoke_events) == 1
    assert issue_events[0].actor == "dev" and issue_events[0].detail["name"] == "editor"
    assert "token" not in issue_events[0].detail
    assert revoke_events[0].detail["token_id"] == token_id


@pytest.mark.parametrize("role", ["approver", "member"])
async def test_non_owner_members_get_404(
    client: httpx.AsyncClient, engine: AsyncEngine, role: str
) -> None:
    slug = _slug()
    await register_repo(engine, f"e2e/{slug}-repo", {"dev": "owner", "u": role}, team=slug)
    path = f"/api/workspaces/{slug}/mcp-tokens"
    headers = {"X-Dev-User": "u"}

    assert (await client.post(path, json={"name": "x"}, headers=headers)).status_code == 404
    assert (await client.get(path, headers=headers)).status_code == 404
    assert (await client.delete(f"{path}/{uuid.uuid4()}", headers=headers)).status_code == 404


async def test_non_member_gets_404(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug = _slug()
    await register_repo(engine, f"e2e/{slug}-repo", {"dev": "owner"}, team=slug)
    path = f"/api/workspaces/{slug}/mcp-tokens"
    headers = {"X-Dev-User": "outsider"}

    assert (await client.post(path, json={"name": "x"}, headers=headers)).status_code == 404
    assert (await client.get(path, headers=headers)).status_code == 404
    assert (await client.delete(f"{path}/{uuid.uuid4()}", headers=headers)).status_code == 404

    # 存在しないワークスペースも同じ 404（存在を漏らさない）
    missing = f"/api/workspaces/{slug}-nope/mcp-tokens"
    assert (await client.get(missing, headers={"X-Dev-User": "dev"})).status_code == 404


async def test_owner_cannot_revoke_other_workspace_token(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """A の owner が B のトークン ID を A の URL で失効しようとしても 404。"""
    slug_a = _slug()
    slug_b = _slug()
    await register_repo(engine, f"e2e/{slug_a}-repo", {"dev": "owner"}, team=slug_a)
    await register_repo(engine, f"e2e/{slug_b}-repo", {"dev": "owner"}, team=slug_b)

    issued_b = await client.post(
        f"/api/workspaces/{slug_b}/mcp-tokens",
        json={"name": "b-token"},
        headers={"X-Dev-User": "dev"},
    )
    token_id_b = issued_b.json()["id"]

    cross = await client.delete(
        f"/api/workspaces/{slug_a}/mcp-tokens/{token_id_b}", headers={"X-Dev-User": "dev"}
    )
    assert cross.status_code == 404

    # B のトークンは失効していない
    listing_b = await client.get(
        f"/api/workspaces/{slug_b}/mcp-tokens", headers={"X-Dev-User": "dev"}
    )
    assert listing_b.json()[0]["revoked_at"] is None
