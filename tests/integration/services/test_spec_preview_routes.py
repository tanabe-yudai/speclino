"""`POST /api/workspaces/{slug}/specs/preview`（S66）の統合試験。

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
from tests.integration.services.conftest import register_repo
from tests.unit.spec.uml.samples import FULL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

MEMBER = {"X-Dev-User": "member1"}
STRANGER = {"X-Dev-User": "owner9"}  # 別のワークスペースの owner

Ctx = tuple[httpx.AsyncClient, str]


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-pv-{uuid.uuid4().hex[:8]}"
    other = f"e2e-pvo-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}", {"owner1": "owner", "member1": "member"}, slug)
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
        yield c, slug


def _url(slug: str) -> str:
    return f"/api/workspaces/{slug}/specs/preview"


async def test_member_gets_parse_result_and_canonical(ctx: Ctx) -> None:
    c, slug = ctx
    res = await c.post(_url(slug), json={"markdown": FULL}, headers=MEMBER)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["parse_result"]["compiled"] is not None
    assert "```mermaid" in body["canonical"]


async def test_error_returns_null_compiled_with_locations(ctx: Ctx) -> None:
    c, slug = ctx
    res = await c.post(
        _url(slug), json={"markdown": "```mermaid\n%%@ id: x\nsequenceDiagram\n"}, headers=MEMBER
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["parse_result"]["compiled"] is None
    assert body["canonical"] is None
    errors = [d for d in body["parse_result"]["diagnostics"] if d["severity"] == "error"]
    assert any(d["span"] and d["span"]["line"] >= 1 for d in errors)


async def test_other_workspace_is_404(ctx: Ctx) -> None:
    c, slug = ctx
    res = await c.post(_url(slug), json={"markdown": FULL}, headers=STRANGER)
    assert res.status_code == 404
    missing = "no-such-ws-" + uuid.uuid4().hex[:6]
    res = await c.post(_url(missing), json={"markdown": FULL}, headers=MEMBER)
    assert res.status_code == 404


async def test_without_dev_user_is_not_a_member(ctx: Ctx) -> None:
    # env=local では認証ヘッダが無いと既定の開発用利用者になる。メンバーでないので 404
    c, slug = ctx
    res = await c.post(_url(slug), json={"markdown": FULL})
    assert res.status_code == 404


async def test_body_over_limit_is_413(ctx: Ctx) -> None:
    c, slug = ctx
    huge = "a" * (512 * 1024 + 1)
    res = await c.post(_url(slug), json={"markdown": huge}, headers=MEMBER)
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


async def test_bad_body_is_422(ctx: Ctx) -> None:
    c, slug = ctx
    headers = {**MEMBER, "content-type": "application/json"}
    res = await c.post(_url(slug), content=b"{not json", headers=headers)
    assert res.status_code == 422
    res = await c.post(_url(slug), json={"markdown": "x", "compiled": {}}, headers=MEMBER)
    assert res.status_code == 422
