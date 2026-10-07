"""S68・S75: sync トークンと `POST /api/sync/bundle`（api/routes/sync.py）と `speclino sync`。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import asyncio
import json
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
from typer.testing import CliRunner

from speclino import cli, cli_sync, sync_apply
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.domain.finding import Finding
from speclino.domain.knowledge_sync import SyncPlan
from speclino.services import knowledge_registry as kr
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


async def _approved(x: Ctx, kind: str, name: str, content: dict[str, object]) -> dict[str, object]:
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
    return dict(body)


async def _revise(x: Ctx, active: dict[str, object], content: dict[str, object]) -> None:
    """active の改訂を content に書き換えて承認する（新しい bundle の世代になる）。"""
    aid = active["artifact_id"]
    put = await x.c.put(
        f"/api/workspaces/{x.slug}/artifacts/{aid}/draft",
        json={
            "expected_revision": active["revision"],
            "expected_hash": active["content_hash"],
            "content": content,
        },
        headers=MEMBER,
    )
    assert put.status_code == 200, put.text
    draft = put.json()
    ok = await x.c.post(
        f"/api/workspaces/{x.slug}/artifacts/{aid}/approve",
        json={"expected_revision": draft["revision"], "expected_hash": draft["content_hash"]},
        headers=OWNER,
    )
    assert ok.status_code == 200, ok.text


def _skill_content(body: str, project_id: str) -> dict[str, object]:
    return {
        "kind": "skill",
        "skill_md": f"---\nname: review\ndescription: d\n---\n{body}\n",
        "files": {"ref/check.md": "チェック"},
        "scope": {"projects": [project_id]},
    }


async def _seed(x: Ctx) -> dict[str, object]:
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
    return await _approved(x, "skill", "review", _skill_content("手順", x.project_id))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post_bundle(
    x: Ctx, token: str, repo_id: int, lock_json: str | None = None
) -> httpx.Response:
    return await x.c.post(
        "/api/sync/bundle",
        json={"repo_id": repo_id, "lock_json": lock_json},
        headers=_bearer(token),
    )


async def _check(x: Ctx, repo: Path) -> Finding:
    """手元の checkout の lock を、今の bundle（このプロジェクトに当てたもの）で照合する。"""
    async with async_sessionmaker(x.engine, expire_on_commit=False)() as s, s.begin():
        bundle = await kr.ensure_bundle(s, x.slug)
        resolved = await kr.resolve_for_project(s, bundle, x.project_id)
    lock_path = repo / ks.LOCK_PATH
    return ks.check_lock(
        repo,
        lock_path.read_text() if lock_path.exists() else None,
        current=resolved.bundle,
        current_members=ks.project_members(resolved),
        github_repo_id=repo_id_of(x.repo),
    )


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
    gone = await _post_bundle(x, token, repo_id_of(x.repo))
    assert gone.status_code == 401


async def test_sync_bundle_writes_and_verifies(ctx: Ctx, tmp_path: Path) -> None:
    x = ctx
    await _seed(x)
    token = (await _issue(x)).json()["token"]
    res = await _post_bundle(x, token, repo_id_of(x.repo))
    assert res.status_code == 200, res.text
    assert token not in res.text
    plan = SyncPlan.model_validate(res.json())
    assert ks.verify_plan(plan) and ks.verify_lock(plan.lock)
    ops = {op.path: op for op in plan.ops}
    assert ops["AGENTS.md"].op == "set_managed_block"
    assert ops["AGENTS.md"].inner == "共通\n"  # type: ignore[union-attr]
    assert ops[".claude/skills/review/ref/check.md"].content == "チェック"  # type: ignore[union-attr]
    assert ".agents/skills/review/SKILL.md" in ops
    assert plan.bundle_generation == plan.lock.bundle_generation == 2
    assert plan.github_repo_id == plan.lock.github_repo_id == repo_id_of(x.repo)
    assert plan.repo_full_name == x.repo
    assert {m.name for m in plan.lock.members} == {"agents", "review"}

    repo = tmp_path / "checkout"
    repo.mkdir()
    assert sync_apply.apply_plan(repo, res.json()).ok
    assert (await _check(x, repo)).verdict == "VERIFIED_IN_SCOPE"

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
    other = await _post_bundle(x, token, repo_id_of(x.other_repo))
    assert other.status_code == 404
    missing = await _post_bundle(x, token, 1)
    assert missing.status_code == 404
    no_token = await x.c.post("/api/sync/bundle", json={"repo_id": repo_id_of(x.repo)})
    assert no_token.status_code == 401
    mcp = await x.c.post(f"/api/workspaces/{x.slug}/mcp-tokens", json={"name": "m"}, headers=OWNER)
    wrong_kind = await _post_bundle(x, mcp.json()["token"], repo_id_of(x.repo))
    assert wrong_kind.status_code == 401


async def test_sync_bundle_without_knowledge_is_empty(ctx: Ctx) -> None:
    x = ctx
    token = (await _issue(x)).json()["token"]
    res = await _post_bundle(x, token, repo_id_of(x.repo))
    assert res.status_code == 200, res.text
    assert res.json()["ops"] == []
    assert res.json()["lock"]["bundle_generation"] == 1


class _Bridge(httpx.BaseTransport):
    """同期の httpx（CLI）の要求を、テストのイベントループの ASGI アプリに渡す。"""

    def __init__(self, client: httpx.AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def go() -> httpx.Response:
            forwarded = httpx.Request(
                request.method, request.url, headers=request.headers, content=request.read()
            )
            res = await self.client.send(forwarded)
            await res.aread()
            return res

        res = asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout=60)
        return httpx.Response(res.status_code, headers=res.headers, content=res.content)


async def test_cli_sync_round_trip(
    ctx: Ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """承認 → 取り込み → VERIFIED → 改訂の承認 → REVIEW → 取り込み → VERIFIED → スキルを外す
    → 取り込みでディレクトリが消えて VERIFIED（S75 の完了条件）。"""
    x = ctx
    review = await _seed(x)
    token = (await _issue(x)).json()["token"]
    monkeypatch.setenv(cli_sync.TOKEN_ENV, token)
    monkeypatch.setenv(cli_sync.URL_ENV, "http://t")
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(cli_sync, "transport_factory", lambda: _Bridge(x.c, loop))
    runner = CliRunner()
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# 手書きのメモ\n")

    async def sync(*extra: str) -> None:
        args = ["sync", "--repo", str(repo), *extra]
        result = await asyncio.to_thread(runner.invoke, cli.app, args)
        assert result.exit_code == 0, result.output
        assert token not in result.output

    await sync("--repo-id", str(repo_id_of(x.repo)))
    assert (await _check(x, repo)).verdict == "VERIFIED_IN_SCOPE"
    assert (repo / ".claude/skills/review/ref/check.md").read_text() == "チェック"

    await _revise(x, review, _skill_content("新しい手順", x.project_id))
    assert (await _check(x, repo)).verdict == "REVIEW"
    await sync()
    assert (await _check(x, repo)).verdict == "VERIFIED_IN_SCOPE"
    assert (repo / ".agents/skills/review/SKILL.md").read_text().endswith("新しい手順\n")

    # スキルを同じワークスペースの別のプロジェクトに移す（このリポジトリからは外れる）
    other = f"{x.repo}-b"
    await register_repo(x.engine, other, {"owner1": "owner"}, x.slug)
    async with async_sessionmaker(x.engine, expire_on_commit=False)() as s:
        arts = {a.name: a for a in await kr.list_artifacts(s, x.slug)}
        active = await kr.revision_at(
            s, str(review["artifact_id"]), arts["review"].active_revision or 1
        )
    moved = {**review, "revision": active.revision, "content_hash": active.content_hash}
    await _revise(x, moved, _skill_content("新しい手順", str(await project_id_of(x.engine, other))))
    await sync()
    assert not (repo / ".claude/skills/review").exists()
    assert not (repo / ".agents/skills/review").exists()
    lock = json.loads((repo / ks.LOCK_PATH).read_text())
    assert ".claude/skills/review/SKILL.md" in lock["absent"]
    assert (await _check(x, repo)).verdict == "VERIFIED_IN_SCOPE"
    assert (repo / "AGENTS.md").read_text().startswith("# 手書きのメモ\n")
