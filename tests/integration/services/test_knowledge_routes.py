"""`api/routes/knowledge.py` の統合試験（S62）。fixture は test_policies_api.py と同じ作り。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from typer.testing import CliRunner

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.cli import app as cli_app
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

MEMBER = {"X-Dev-User": "member1"}
OWNER = {"X-Dev-User": "owner1"}
STRANGER = {"X-Dev-User": "owner9"}
SEED_SOURCE = (
    Path(__file__).resolve().parents[3] / ".speclino" / "policy" / "speclino-core.yaml"
)  # 別のワークスペースの owner

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
        f"/api/workspaces/{slug}/artifacts",
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
    listed = (await c.get(f"/api/workspaces/{slug}/artifacts", headers=MEMBER)).json()
    assert [(a["name"], a["draft_revision"]) for a in listed] == [("agents", 1)]
    # 一覧のスコープは有効な改訂、無ければ下書きのもの
    assert listed[0]["scope"] == {"workspace": True, "projects": []}

    put = await c.put(
        f"/api/workspaces/{slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": 1,
            "expected_hash": created["content_hash"],
            "content": _instr("直した"),
        },
        headers=MEMBER,
    )
    assert put.status_code == 200, put.text
    draft = (await c.get(f"/api/workspaces/{slug}/artifacts/{aid}/draft", headers=MEMBER)).json()
    assert draft["content"]["body"] == "直した"

    denied = await c.post(
        f"/api/workspaces/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": draft["content_hash"]},
        headers=MEMBER,
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "not_an_approver"
    stale = await c.post(
        f"/api/workspaces/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": "sha256:" + "0" * 64},
        headers=OWNER,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "draft_changed"
    ok = await c.post(
        f"/api/workspaces/{slug}/artifacts/{aid}/approve",
        json={"expected_revision": 1, "expected_hash": draft["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["bundle"]["generation"] == 1
    assert ok.json()["revision"]["self_approved"] is False
    after = (await c.get(f"/api/workspaces/{slug}/artifacts", headers=MEMBER)).json()
    assert after[0]["scope"] == {"workspace": True, "projects": []}

    current = (await c.get(f"/api/workspaces/{slug}/bundles/current", headers=MEMBER)).json()
    assert current == ok.json()["bundle"]
    revs = (await c.get(f"/api/workspaces/{slug}/artifacts/{aid}/revisions", headers=MEMBER)).json()
    assert [(r["revision"], r["status"]) for r in revs] == [(1, "active")]
    no_draft = await c.get(f"/api/workspaces/{slug}/artifacts/{aid}/draft", headers=MEMBER)
    assert no_draft.status_code == 404


async def test_other_workspace_is_404(ctx: Ctx) -> None:
    c, slug, other, _ = ctx
    aid = (await _create(c, slug))["artifact_id"]
    calls: list[tuple[str, str, object]] = [
        ("GET", f"/api/workspaces/{slug}/artifacts", None),
        (
            "POST",
            f"/api/workspaces/{slug}/artifacts",
            {"kind": "instructions", "name": "x", "content": _instr()},
        ),
        ("GET", f"/api/workspaces/{slug}/artifacts/{aid}/draft", None),
        (
            "PUT",
            f"/api/workspaces/{slug}/artifacts/{aid}/draft",
            {"expected_revision": 1, "expected_hash": "h", "content": _instr()},
        ),
        (
            "POST",
            f"/api/workspaces/{slug}/artifacts/{aid}/approve",
            {"expected_revision": 1, "expected_hash": "h"},
        ),
        ("GET", f"/api/workspaces/{slug}/artifacts/{aid}/revisions", None),
        ("GET", f"/api/workspaces/{slug}/bundles/current", None),
    ]
    for method, path, body in calls:
        res = await c.request(method, path, json=body, headers=STRANGER)
        assert res.status_code == 404, (method, path, res.text)
    # 自分のワークスペースの URL に、別のワークスペースの artifact ID を入れても 404
    for method, path, body in calls[2:6]:
        res = await c.request(
            method,
            path.replace(f"/workspaces/{slug}/", f"/workspaces/{other}/"),
            json=body,
            headers=STRANGER,
        )
        assert res.status_code == 404, (method, path, res.text)
    garbage = await c.get(f"/api/workspaces/{slug}/artifacts/not-a-uuid/draft", headers=MEMBER)
    assert garbage.status_code == 404


async def test_errors_are_mapped(ctx: Ctx) -> None:
    c, slug, _, pid = ctx
    await _create(c, slug)
    dup = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={"kind": "instructions", "name": "agents", "content": _instr()},
        headers=MEMBER,
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "artifact_exists"
    marker = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={"kind": "instructions", "name": "m", "content": _instr("<!-- speclino:end -->")},
        headers=MEMBER,
    )
    assert marker.status_code == 422
    forged = await c.post(
        f"/api/workspaces/{slug}/artifacts",
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
        f"/api/workspaces/{slug}/artifacts",
        json={"kind": "instructions", "name": "e", "content": _instr(), "status": "active"},
        headers=MEMBER,
    )
    assert extra.status_code == 422

    spec = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={
            "kind": "spec",
            "name": "orders",
            "content": {
                "kind": "spec",
                "markdown": "# 仕様\n\n```mermaid\n%%@ id: s\nsequenceDiagram\n    par\n```\n",
                "scope": {"projects": [pid]},
            },
        },
        headers=MEMBER,
    )
    assert spec.status_code == 200, spec.text
    s = spec.json()
    # 図に誤りがある spec は承認できない（S63 の parser が診断する）
    res = await c.post(
        f"/api/workspaces/{slug}/artifacts/{s['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": s["content_hash"]},
        headers=OWNER,
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "spec_not_compilable"
    plain = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={
            "kind": "spec",
            "name": "plain",
            "content": {"kind": "spec", "markdown": "# 仕様\n", "scope": {"projects": [pid]}},
        },
        headers=MEMBER,
    )
    approved = await c.post(
        f"/api/workspaces/{slug}/artifacts/{plain.json()['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": plain.json()["content_hash"]},
        headers=OWNER,
    )
    assert approved.status_code == 200, approved.text

    a = await _create(c, slug, "agents-a")
    b = (
        await c.get(f"/api/workspaces/{slug}/artifacts/{a['artifact_id']}/draft", headers=MEMBER)
    ).json()
    first = await c.get(f"/api/workspaces/{slug}/artifacts", headers=MEMBER)
    agents = next(x for x in first.json() if x["name"] == "agents")
    d = (
        await c.get(
            f"/api/workspaces/{slug}/artifacts/{agents['artifact_id']}/draft", headers=MEMBER
        )
    ).json()
    ok = await c.post(
        f"/api/workspaces/{slug}/artifacts/{agents['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": d["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text
    conflict = await c.post(
        f"/api/workspaces/{slug}/artifacts/{a['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": b["content_hash"]},
        headers=OWNER,
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "bundle_conflict"


async def test_policy_activation_conflict_is_409_and_seed_explains(
    ctx: Ctx, engine: AsyncEngine
) -> None:
    """規約の有効化は bundle を作り直す。作れなければ（スコープのプロジェクトが別のワークスペースへ
    移った）規約の承認も止まり、API は 409、CLI の seed は理由を出して終了コード 2。"""
    c, slug, other, pid = ctx
    res = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={
            "kind": "instructions",
            "name": "only-a",
            "content": {**_instr(), "scope": {"projects": [pid]}},
        },
        headers=MEMBER,
    )
    ok = await c.post(
        f"/api/workspaces/{slug}/artifacts/{res.json()['artifact_id']}/approve",
        json={"expected_revision": 1, "expected_hash": res.json()["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE projects SET team = :t WHERE id = CAST(:p AS uuid)"),
            {"t": other, "p": pid},
        )

    seeded = await asyncio.to_thread(
        CliRunner().invoke,
        cli_app,
        ["policy", "seed", "--team", slug, "--from", str(SEED_SOURCE)],
    )
    assert seeded.exit_code == 2, seeded.output
    assert "知識の bundle" in seeded.output

    rule = {
        "id": "DOC-001",
        "version": "1.0.0",
        "level": "advisory",
        "title": "題",
        "text": "本文",
        "applies_to": {"paths": ["docs/**"]},
        "verifier": None,
        "on_missing_input": "unknown",
    }
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [rule]}
    saved = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg, "note": "", "expected_revision": None, "expected_hash": None},
        headers=MEMBER,
    )
    assert saved.status_code == 200, saved.text
    view = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    res = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": saved.json()["revision"], "expected_hash": view["draft_hash"]},
        headers=OWNER,
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "bundle_conflict", res.text


async def _audit_details(engine: AsyncEngine, slug: str, action: str) -> list[dict[str, object]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT detail::text FROM audit_events WHERE action = :a AND target LIKE :t "
                    "ORDER BY ts"
                ),
                {"a": action, "t": f"{slug}%"},
            )
        ).all()
    return [json.loads(r[0]) for r in rows]


async def test_note_is_saved_with_the_draft(ctx: Ctx, engine: AsyncEngine) -> None:
    """S73: 改訂の note（500 文字まで）。要約から入れたかは本人の申告として監査に残す。"""
    c, slug, _, _ = ctx
    res = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={
            "kind": "instructions",
            "name": "noted",
            "content": _instr(),
            "note": "何を: 最初の版",
            "note_source": "chat_summary",
        },
        headers=MEMBER,
    )
    assert res.status_code == 200, res.text
    rev = res.json()
    assert rev["note"] == "何を: 最初の版" and rev["note_source"] == "chat_summary"
    aid = rev["artifact_id"]
    res = await c.put(
        f"/api/workspaces/{slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": 1,
            "expected_hash": rev["content_hash"],
            "content": _instr("新しい本文"),
            "note": "手で書いた説明",
        },
        headers=MEMBER,
    )
    assert res.status_code == 200, res.text
    revisions = (
        await c.get(f"/api/workspaces/{slug}/artifacts/{aid}/revisions", headers=MEMBER)
    ).json()
    assert revisions[0]["note"] == "手で書いた説明" and revisions[0]["note_source"] == "manual"
    # 照合に外れた保存では note も変わらない
    stale = await c.put(
        f"/api/workspaces/{slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": 1,
            "expected_hash": rev["content_hash"],
            "content": _instr("別の本文"),
            "note": "上書き",
        },
        headers=MEMBER,
    )
    assert stale.status_code == 409
    too_long = await c.put(
        f"/api/workspaces/{slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": 1,
            "expected_hash": res.json()["content_hash"],
            "content": _instr("x"),
            "note": "あ" * 501,
        },
        headers=MEMBER,
    )
    assert too_long.status_code == 422
    details = await _audit_details(engine, slug, "knowledge.draft")
    assert [d.get("note_source") for d in details] == ["chat_summary", "manual"]
    assert all("note" not in d for d in details)


async def test_policy_note_source_is_audited(ctx: Ctx, engine: AsyncEngine) -> None:
    c, slug, _, _ = ctx
    package = {
        "id": "team",
        "version": "1",
        "status": "draft",
        "owner": slug,
        "rules": [],
    }
    res = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": package, "note": "要約から", "note_source": "chat_summary"},
        headers=MEMBER,
    )
    assert res.status_code == 200, res.text
    (detail,) = await _audit_details(engine, slug, "policy.draft")
    assert detail["note_source"] == "chat_summary"


async def test_approval_checks_the_note_the_approver_saw(ctx: Ctx) -> None:
    """S73: note は content_hash に入らないので、承認者が見た note のハッシュも照合する。"""
    import hashlib

    c, slug, _, _ = ctx
    res = await c.post(
        f"/api/workspaces/{slug}/artifacts",
        json={"kind": "instructions", "name": "noted2", "content": _instr(), "note": "説明 A"},
        headers=MEMBER,
    )
    rev = res.json()
    seen = "sha256:" + hashlib.sha256("古い説明".encode()).hexdigest()
    url = f"/api/workspaces/{slug}/artifacts/{rev['artifact_id']}/approve"
    body = {"expected_revision": 1, "expected_hash": rev["content_hash"]}
    stale = await c.post(url, json={**body, "expected_note_hash": seen}, headers=OWNER)
    assert stale.status_code == 409
    right = "sha256:" + hashlib.sha256("説明 A".encode()).hexdigest()
    ok = await c.post(url, json={**body, "expected_note_hash": right}, headers=OWNER)
    assert ok.status_code == 200, ok.text


async def test_policy_approval_checks_the_note(ctx: Ctx) -> None:
    import hashlib

    c, slug, _, _ = ctx
    package = {"id": "team", "version": "1", "status": "draft", "owner": slug, "rules": []}
    await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": package, "note": "説明 P"},
        headers=MEMBER,
    )
    view = (await c.get(f"/api/workspaces/{slug}/policies", headers=OWNER)).json()
    body = {"expected_revision": view["draft"]["revision"], "expected_hash": view["draft_hash"]}
    url = f"/api/workspaces/{slug}/policies/draft/approve"
    wrong = "sha256:" + hashlib.sha256(b"x").hexdigest()
    assert (
        await c.post(url, json={**body, "expected_note_hash": wrong}, headers=OWNER)
    ).status_code == 409
    right = "sha256:" + hashlib.sha256("説明 P".encode()).hexdigest()
    res = await c.post(url, json={**body, "expected_note_hash": right}, headers=OWNER)
    assert res.status_code == 200, res.text
