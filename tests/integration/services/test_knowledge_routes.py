"""`api/routes/knowledge.py` の統合試験（S62）。fixture は test_policies_api.py と同じ作り。

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
from tests.integration.services.conftest import project_id_of, register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

MEMBER = {"X-Dev-User": "member1"}
OWNER = {"X-Dev-User": "owner1"}
STRANGER = {"X-Dev-User": "owner9"}  # 別のワークスペースの owner

Ctx = tuple[httpx.AsyncClient, str, str, str]


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-kn-{uuid.uuid4().hex[:8]}"
    other = f"e2e-kno-{uuid.uuid4().hex[:8]}"
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
        yield c, slug, other, str(await project_id_of(engine, f"e2e/{slug}"))


def _instr(body: str = "本文") -> dict[str, object]:
    return {
        "kind": "instructions",
        "target": "agents_md",
        "body": body,
        "scope": {"workspace": True},
    }


async def _create(c: httpx.AsyncClient, slug: str, name: str = "agents") -> dict[str, object]:
    res = await c.post(
        f"/api/w/{slug}/artifacts",
        json={"kind": "instructions", "name": name, "content": _instr()},
        headers=MEMBER,
    )
    assert res.status_code == 200, res.text
    body: dict[str, object] = res.json()
    return body


async def test_create_update_approve_and_bundle(ctx: Ctx) -> None:
    c, slug, _, _ = ctx
    created = await _create(c, slug)
    aid = created["artifact_id"]
    assert created["revision"] == 1 and created["status"] == "draft"
    listed = (await c.get(f"/api/w/{slug}/artifacts", headers=MEMBER)).json()
    assert [(a["name"], a["draft_revision"]) for a in listed] == [("agents", 1)]

    put = await c.put(
        f"/api/w/{slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": 1,
            "expected_hash": created["content_hash"],
            "content": _instr("直した"),
        },
        headers=MEMBER,
    )
    assert put.status_code == 200, put.text
    draft = (await c.get(f"/api/w/{slug}/artifacts/{aid}/draft", headers=MEMBER)).json()
    assert draft["content"]["body"] == "直した"

    denied = await c.post(
        f"/api/w/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": draft["content_hash"]},
        headers=MEMBER,
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "not_an_approver"
    stale = await c.post(
        f"/api/w/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": "sha256:" + "0" * 64},
        headers=OWNER,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "draft_changed"
    ok = await c.post(
        f"/api/w/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": draft["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["bundle"]["generation"] == 1
    assert ok.json()["revision"]["self_approved"] is False

    current = (await c.get(f"/api/w/{slug}/bundles/current", headers=MEMBER)).json()
    assert current == ok.json()["bundle"]
    revs = (await c.get(f"/api/w/{slug}/artifacts/{aid}/revisions", headers=MEMBER)).json()
    assert [(r["revision"], r["status"]) for r in revs] == [(1, "active")]
    no_draft = await c.get(f"/api/w/{slug}/artifacts/{aid}/draft", headers=MEMBER)
    assert no_draft.status_code == 404


async def test_other_workspace_is_404(ctx: Ctx) -> None:
    c, slug, other, _ = ctx
    aid = (await _create(c, slug))["artifact_id"]
    calls: list[tuple[str, str, object]] = [
        ("GET", f"/api/w/{slug}/artifacts", None),
        (
            "POST",
            f"/api/w/{slug}/artifacts",
            {"kind": "instructions", "name": "x", "content": _instr()},
        ),
        ("GET", f"/api/w/{slug}/artifacts/{aid}/draft", None),
        (
            "PUT",
            f"/api/w/{slug}/artifacts/{aid}/draft",
            {"expected_revision": 1, "expected_hash": "h", "content": _instr()},
        ),
        (
            "POST",
            f"/api/w/{slug}/artifacts/{aid}/approve",
            {"expected_revision": 1, "expected_hash": "h"},
        ),
        ("GET", f"/api/w/{slug}/artifacts/{aid}/revisions", None),
        ("GET", f"/api/w/{slug}/bundles/current", None),
    ]
    for method, path, body in calls:
        res = await c.request(method, path, json=body, headers=STRANGER)
        assert res.status_code == 404, (method, path, res.text)
    # 自分のワークスペースの URL に、別のワークスペースの artifact ID を入れても 404
    for method, path, body in calls[2:6]:
        res = await c.request(
            method, path.replace(f"/w/{slug}/", f"/w/{other}/"), json=body, headers=STRANGER
        )
        assert res.status_code == 404, (method, path, res.text)
    garbage = await c.get(f"/api/w/{slug}/artifacts/not-a-uuid/draft", headers=MEMBER)
    assert garbage.status_code == 404


async def test_errors_are_mapped(ctx: Ctx) -> None:
    c, slug, _, pid = ctx
    await _create(c, slug)
    dup = await c.post(
        f"/api/w/{slug}/artifacts",
        json={"kind": "instructions", "name": "agents", "content": _instr()},
        headers=MEMBER,
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "artifact_exists"
    marker = await c.post(
        f"/api/w/{slug}/artifacts",
        json={"kind": "instructions", "name": "m", "content": _instr("<!-- speclino:end -->")},
        headers=MEMBER,
    )
    assert marker.status_code == 422
    forged = await c.post(
        f"/api/w/{slug}/artifacts",
        json={
            "kind": "spec",
            "name": "orders",
            "content": {
                "kind": "spec",
                "markdown": "# 仕様",
                "scope": {"projects": [pid]},
                "compiled": {
                    "parser_version": "x",
                    "source_hash": "sha256:" + "0" * 64,
                    "ir_hash": "sha256:" + "0" * 64,
                },
            },
        },
        headers=MEMBER,
    )
    assert forged.status_code == 422 and forged.json()["error"]["code"] == "invalid_artifact"
    # クライアントが送った hash・status などは受け付けない（スキーマに無い）
    extra = await c.post(
        f"/api/w/{slug}/artifacts",
        json={"kind": "instructions", "name": "e", "content": _instr(), "status": "active"},
        headers=MEMBER,
    )
    assert extra.status_code == 422

    spec = await c.post(
        f"/api/w/{slug}/artifacts",
        json={
            "kind": "spec",
            "name": "orders",
            "content": {"kind": "spec", "markdown": "# 仕様", "scope": {"projects": [pid]}},
        },
        headers=MEMBER,
    )
    assert spec.status_code == 200, spec.text
    s = spec.json()
    # parser が入るまでは spec を承認できない
    res = await c.post(
        f"/api/w/{slug}/artifacts/{s['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": s["content_hash"]},
        headers=OWNER,
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "spec_not_compilable"

    a = await _create(c, slug, "agents-a")
    b = (await c.get(f"/api/w/{slug}/artifacts/{a['artifact_id']}/draft", headers=MEMBER)).json()
    first = await c.get(f"/api/w/{slug}/artifacts", headers=MEMBER)
    agents = next(x for x in first.json() if x["name"] == "agents")
    d = (
        await c.get(f"/api/w/{slug}/artifacts/{agents['artifact_id']}/draft", headers=MEMBER)
    ).json()
    ok = await c.post(
        f"/api/w/{slug}/artifacts/{agents['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": d["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text
    conflict = await c.post(
        f"/api/w/{slug}/artifacts/{a['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": b["content_hash"]},
        headers=OWNER,
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "bundle_conflict"
