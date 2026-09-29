"""リポジトリ登録（S55）: API → サービス → DB を、GitHub を MockTransport にして通す。"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.github.user_oauth import GitHubUserOAuth
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


@dataclass
class FakeGitHub:
    """GitHub の認可・インストール一覧・失効を再現する。"""

    repos: list[dict[str, Any]] = field(default_factory=list)
    exchange_error: str | None = None
    revoke_status: int = 204
    revoked: list[str] = field(default_factory=list)
    issued: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login/oauth/access_token":
            if self.exchange_error:
                return httpx.Response(200, json={"error": self.exchange_error})
            self.issued += 1
            return httpx.Response(
                200, json={"access_token": f"ghu_secret{self.issued}", "refresh_token": "ghr_r"}
            )
        if path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 99}]})
        if path == "/user/installations/99/repositories":
            return httpx.Response(200, json={"repositories": self.repos})
        if path == "/applications/cid/grant" and request.method == "DELETE":
            self.revoked.append(request.content.decode())
            return httpx.Response(self.revoke_status)
        raise AssertionError(f"unexpected {request.method} {request.url}")


def _repo(name: str, repo_id: int, admin: bool = True) -> dict[str, Any]:
    return {
        "id": repo_id,
        "full_name": name,
        "default_branch": "trunk",
        "permissions": {"admin": admin},
    }


@pytest_asyncio.fixture
async def ctx(
    engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeGitHub, str]]:
    slug = f"e2e-reg-{uuid.uuid4().hex[:8]}"
    await register_repo(
        engine,
        f"e2e/reg-seed-{uuid.uuid4().hex[:6]}",
        {"owner": "owner", "mem": "member"},
        team=slug,
    )
    fake = FakeGitHub()
    oauth = GitHubUserOAuth("cid", "csecret", transport=httpx.MockTransport(fake.handle))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type, return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    settings = Settings(env="local", role="all", code_host_kind="local", public_url="http://t")
    state = AppState(settings, engine, factory, host, queue, blobs, service, None, oauth)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t",
            headers={"X-Dev-User": "owner"},
        ) as c,
    ):
        yield c, fake, slug


async def _start(c: httpx.AsyncClient, slug: str, repo: str) -> str:
    res = await c.post(
        f"/api/workspaces/{slug}/repositories/authorize", json={"repo_full_name": repo}
    )
    assert res.status_code == 200, res.text
    q = parse_qs(urlparse(res.json()["authorize_url"]).query)
    assert q["redirect_uri"] == ["http://t/github/callback"]
    return q["state"][0]


async def test_an_owner_registers_an_installed_repository_they_administer(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str], engine: AsyncEngine
) -> None:
    c, fake, slug = ctx
    repo = f"e2e/reg-{uuid.uuid4().hex[:8]}"
    fake.repos = [_repo(repo, 7000 + uuid.uuid4().int % 100000)]
    state = await _start(c, slug, repo)

    res = await c.post("/api/github/callback", json={"code": "c1", "state": state})
    assert res.json() == {"slug": slug, "repo_full_name": repo, "status": "registered"}, res.text
    listed = (await c.get(f"/api/workspaces/{slug}/repositories")).json()
    assert any(r["repo_full_name"] == repo and r["default_branch"] == "trunk" for r in listed)
    # トークンは失効させ、DB（試行・監査）に残さない
    assert len(fake.revoked) == 1 and "ghu_secret1" in fake.revoked[0]
    async with engine.connect() as conn:
        leaked = (
            await conn.execute(
                sql_text(
                    "SELECT count(*) FROM audit_events WHERE detail::text LIKE '%ghu_%' "
                    "OR detail::text LIKE '%ghr_%'"
                )
            )
        ).scalar_one()
        project = (
            await conn.execute(
                sql_text("SELECT team, installation_id FROM projects WHERE repo_full_name = :r"),
                {"r": repo},
            )
        ).one()
    assert leaked == 0 and tuple(project) == (slug, 99)

    # 同じ state は二度と使えない（新しい code と組み合わせても）
    again = await c.post("/api/github/callback", json={"code": "c2", "state": state})
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_state"


async def test_the_state_is_bound_to_the_user_who_started(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str],
) -> None:
    c, fake, slug = ctx
    state = await _start(c, slug, "e2e/whatever")
    res = await c.post(
        "/api/github/callback", json={"code": "c", "state": state}, headers={"X-Dev-User": "mem"}
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_state"
    assert fake.issued == 0  # GitHub に行く前に拒否


@pytest.mark.parametrize(
    ("repos", "code"),
    [([], "not_installed"), ([_repo("e2e/reg-na", 5, admin=False)], "not_admin")],
)
async def test_uninstalled_or_non_admin_repositories_are_refused_and_the_token_is_revoked(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str], repos: list[dict[str, Any]], code: str
) -> None:
    c, fake, slug = ctx
    fake.repos = repos
    state = await _start(c, slug, "e2e/reg-na")
    res = await c.post("/api/github/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json()["error"]["code"] == code
    assert len(fake.revoked) == 1


async def test_a_denied_authorization_is_reported(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str],
) -> None:
    c, fake, slug = ctx
    fake.exchange_error = "bad_verification_code"
    state = await _start(c, slug, "e2e/x")
    res = await c.post("/api/github/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json()["error"]["code"] == "github_denied"
    assert "bad_verification_code" in res.json()["error"]["message"] and fake.revoked == []


async def test_a_repository_of_another_workspace_is_refused_without_side_effects(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str], engine: AsyncEngine
) -> None:
    c, fake, slug = ctx
    other = f"e2e-reg-other-{uuid.uuid4().hex[:6]}"
    repo = f"e2e/reg-taken-{uuid.uuid4().hex[:6]}"
    await register_repo(engine, repo, {}, team=other)
    async with engine.connect() as conn:
        repo_id = (
            await conn.execute(
                sql_text("SELECT github_repo_id FROM projects WHERE repo_full_name = :r"),
                {"r": repo},
            )
        ).scalar_one()
    fake.repos = [_repo(repo, repo_id)]
    state = await _start(c, slug, repo)
    res = await c.post("/api/github/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json()["error"]["code"] == "registered_elsewhere"
    async with engine.connect() as conn:
        team = (
            await conn.execute(
                sql_text("SELECT team FROM projects WHERE repo_full_name = :r"), {"r": repo}
            )
        ).scalar_one()
    assert team == other and len(fake.revoked) == 1


async def test_only_owners_start_and_only_members_list(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str],
) -> None:
    c, _, slug = ctx
    member = {"X-Dev-User": "mem"}
    denied = await c.post(
        f"/api/workspaces/{slug}/repositories/authorize",
        json={"repo_full_name": "e2e/x"},
        headers=member,
    )
    assert denied.status_code == 404
    assert (await c.get(f"/api/workspaces/{slug}/repositories", headers=member)).status_code == 200
    outsider = {"X-Dev-User": "nobody"}
    assert (
        await c.get(f"/api/workspaces/{slug}/repositories", headers=outsider)
    ).status_code == 404
    bad = await c.post(
        f"/api/workspaces/{slug}/repositories/authorize", json={"repo_full_name": "no-slash"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_repository"


async def test_nothing_is_registered_when_the_grant_cannot_be_revoked(
    ctx: tuple[httpx.AsyncClient, FakeGitHub, str], engine: AsyncEngine
) -> None:
    """トークンを取り消せなければ、有効なトークンを残したまま成功にしない（登録もしない）。"""
    c, fake, slug = ctx
    repo = f"e2e/reg-norevoke-{uuid.uuid4().hex[:6]}"
    fake.repos = [_repo(repo, 8000 + uuid.uuid4().int % 100000)]
    fake.revoke_status = 500
    state = await _start(c, slug, repo)
    res = await c.post("/api/github/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json()["error"]["code"] == "github_revoke_failed"
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                sql_text("SELECT count(*) FROM projects WHERE repo_full_name = :r"), {"r": repo}
            )
        ).scalar_one()
    assert count == 0
