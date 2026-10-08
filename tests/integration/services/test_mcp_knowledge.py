"""S68・S75: MCP の知識ツール（sync_status / knowledge_pull / spec_get / skill_get）と
sync トークンの制限。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
import pytest
import pytest_asyncio
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino import sync_apply
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.models import Project
from speclino.domain.knowledge import ArtifactScope, InstructionsContent, SkillContent
from speclino.domain.knowledge_sync import SyncIdentity, SyncPlan
from speclino.services import bundle_signing, sync_tokens
from speclino.services import knowledge_registry as kr
from speclino.services import knowledge_sync as ks
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.integration.services.test_knowledge_registry import _spec, stub_compiler
from tests.integration.services.test_mcp_flow import _issue as issue_mcp_token

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


@dataclass
class World:
    engine: AsyncEngine
    f: async_sessionmaker[AsyncSession]
    slug: str
    repo: str
    pid: str
    repo2: str
    other_repo: str
    tmp: Path


@pytest_asyncio.fixture
async def world(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[World]:
    slug = f"e2e-mk-{uuid.uuid4().hex[:8]}"
    other = f"e2e-mko-{uuid.uuid4().hex[:8]}"
    members = {"owner1": "owner", "member1": "member"}
    await register_repo(engine, f"e2e/{slug}-a", members, slug)
    await register_repo(engine, f"e2e/{slug}-b", members, slug)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    yield World(
        engine,
        async_sessionmaker(engine, expire_on_commit=False),
        slug,
        f"e2e/{slug}-a",
        str(await project_id_of(engine, f"e2e/{slug}-a")),
        f"e2e/{slug}-b",
        f"e2e/{other}",
        tmp_path,
    )


async def _approve(w: World, kind: str, name: str, content: object) -> None:
    async with w.f() as s, s.begin():
        rev = await kr.create_draft(
            s,
            workspace=w.slug,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            content=content,  # type: ignore[arg-type]
            author="member1",
        )
    async with w.f() as s, s.begin():
        await kr.approve(
            s,
            artifact_id=rev.artifact_id,
            expected_revision=rev.revision,
            expected_hash=rev.content_hash,
            approver="owner1",
            compiler=stub_compiler,
        )


async def _revise(w: World, name: str, content: object) -> None:
    async with w.f() as s:
        arts = {a.name: a for a in await kr.list_artifacts(s, w.slug)}
        active = await kr.revision_at(s, arts[name].artifact_id, arts[name].active_revision or 1)
    async with w.f() as s, s.begin():
        draft = await kr.update_draft(
            s,
            artifact_id=active.artifact_id,
            expected_revision=active.revision,
            expected_hash=active.content_hash,
            content=content,  # type: ignore[arg-type]
            author="member1",
        )
    async with w.f() as s, s.begin():
        await kr.approve(
            s,
            artifact_id=draft.artifact_id,
            expected_revision=draft.revision,
            expected_hash=draft.content_hash,
            approver="owner1",
            compiler=stub_compiler,
        )


def _skill(body: str, scope: ArtifactScope) -> SkillContent:
    return SkillContent(
        kind="skill",
        skill_md=f"---\nname: review\ndescription: d\n---\n{body}\n",
        files={"ref.md": "参照"},
        scope=scope,
    )


async def _seed(w: World) -> None:
    await _approve(
        w,
        "instructions",
        "agents",
        InstructionsContent(
            kind="instructions",
            body="共通",
            scope=ArtifactScope(workspace=True),
        ),
    )
    await _approve(w, "skill", "review", _skill("手順", ArtifactScope(projects=[w.pid])))
    await _approve(w, "spec", "orders", _spec(ArtifactScope(projects=[w.pid]), "R-ORD-1"))


async def _sync_token(w: World) -> str:
    async with w.f() as s, s.begin():
        issued = await sync_tokens.issue(
            s, slug=w.slug, project_id=uuid.UUID(w.pid), name="t", actor_uid="owner1"
        )
    return issued.token


def _app(w: World) -> Any:
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(w.tmp / "blobs")
    settings = Settings(env="local", role="all", code_host_kind="local")
    service = ReviewRunService(w.f, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    return create_app(settings, AppState(settings, w.engine, w.f, host, queue, blobs, service))


@asynccontextmanager
async def _client(w: World, token: str) -> AsyncIterator[Client]:
    app = _app(w)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http,
        Client(streamable_http_client("http://localhost/mcp", http_client=http)) as client,
    ):
        yield client


def _text(result: Any) -> str:
    return str(getattr(result.content[0], "text", ""))


def _json(result: Any) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    data: dict[str, Any] = json.loads(_text(result))
    return data


async def test_sync_token_cannot_start_verification(world: World) -> None:
    w = world
    token = await _sync_token(w)
    async with _client(w, token) as c:
        refused = await c.call_tool(
            "verification_start", {"repo_full_name": w.repo, "head_sha": "HEAD"}
        )
        assert refused.is_error and "読み取り専用" in _text(refused)
        # 同じワークスペースでも別のプロジェクトは「無い」と同じ
        for repo in (w.repo2, w.other_repo):
            hidden = await c.call_tool("spec_get", {"repo_full_name": repo, "name": "orders"})
            assert hidden.is_error and "登録されていない" in _text(hidden)
    async with w.engine.connect() as conn:
        runs = (
            await conn.execute(
                text("SELECT count(*) FROM review_runs WHERE project_id = :p"), {"p": w.pid}
            )
        ).scalar_one()
    assert runs == 0


async def test_skill_and_spec_get(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with _client(w, token) as c:
        skill = _json(await c.call_tool("skill_get", {"repo_full_name": w.repo, "name": "review"}))
        spec = _json(await c.call_tool("spec_get", {"repo_full_name": w.repo, "name": "orders"}))
        missing = await c.call_tool("skill_get", {"repo_full_name": w.repo, "name": "nope"})
    assert skill["skill_md"].endswith("手順\n") and skill["files"] == {"ref.md": "参照"}
    assert skill["bundle_generation"] == 3
    assert "R-ORD-1" in spec["markdown"] and spec["revision"] == 1
    assert missing.is_error
    async with w.engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT channel FROM knowledge_fetches WHERE workspace = :w"), {"w": w.slug}
            )
        ).all()
    assert [r.channel for r in rows] == ["sync", "sync"]

    # 既存の MCP トークンも読める（ワークスペースの全プロジェクト）
    mcp_token = await issue_mcp_token(w.engine, w.slug)
    async with _client(w, mcp_token) as c:
        got = _json(await c.call_tool("spec_get", {"repo_full_name": w.repo, "name": "orders"}))
        none_for_b = await c.call_tool("spec_get", {"repo_full_name": w.repo2, "name": "orders"})
    assert got["name"] == "orders"
    assert none_for_b.is_error  # orders は a のプロジェクトだけに当たる


async def test_sync_status_reports_stale_lock(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with w.f() as s, s.begin():
        identity = await sync_tokens.authenticate(s, token)
        assert isinstance(identity, SyncIdentity)
        payload = await ks.fetch_for_sync(s, identity, repo_id_of(w.repo))
    lock_json = payload.lock.model_dump_json()
    async with _client(w, token) as c:
        fresh = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
        assert fresh["stale"] is False and fresh["current_generation"] == 3
        await _revise(w, "review", _skill("新しい手順", ArtifactScope(projects=[w.pid])))
        stale = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
        broken = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": "{}"})
        )
    assert stale["stale"] is True and stale["lock_generation"] == 3
    assert stale["changed"] == [
        {"name": "review", "kind": "skill", "lock_revision": 1, "current_revision": 2}
    ]
    assert broken["stale"] is True
    assert stale["verdict"] == "REVIEW" and broken["verdict"] == "UNKNOWN"


async def test_sync_status_rejects_expired_or_other_repo_lock(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with w.f() as s, s.begin():
        identity = await sync_tokens.authenticate(s, token)
        assert isinstance(identity, SyncIdentity)
        payload = await ks.fetch_for_sync(s, identity, repo_id_of(w.repo))
    base = payload.lock

    def resign(**update: Any) -> str:
        lock = base.model_copy(update=update)
        signed = lock.model_copy(update={"signature": bundle_signing.sign(ks.lock_payload(lock))})
        return signed.model_dump_json()

    expired = resign(
        issued_at=base.issued_at - timedelta(days=60),
        expires_at=base.issued_at - timedelta(days=30),
    )
    other_repo = resign(github_repo_id=repo_id_of(w.repo) + 1)
    async with _client(w, token) as c:
        for lock_json, word in ((expired, "期限"), (other_repo, "別のワークスペースかリポジトリ")):
            got = _json(
                await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
            )
            assert got["stale"] is True and got["verdict"] == "UNKNOWN" and word in got["reason"]


# ---------------------------------------------------------------------------
# S75: knowledge_pull と sync_status（エージェントによる取り込み）
# ---------------------------------------------------------------------------


async def _fetch_channels(w: World) -> list[str]:
    async with w.engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT channel FROM knowledge_fetches WHERE workspace = :w"),
                {"w": w.slug},
            )
        ).all()
    return sorted(r.channel for r in rows)


async def _check(w: World, repo: Path, project_id: str, repo_name: str) -> str:
    async with w.f() as s, s.begin():
        bundle = await kr.ensure_bundle(s, w.slug)
        resolved = await kr.resolve_for_project(s, bundle, project_id)
    lock_path = repo / ks.LOCK_PATH
    finding = ks.check_lock(
        repo,
        lock_path.read_text() if lock_path.exists() else None,
        current=resolved.bundle,
        current_members=ks.project_members(resolved),
        github_repo_id=repo_id_of(repo_name),
    )
    return finding.verdict


@asynccontextmanager
async def _http(w: World) -> AsyncIterator[httpx2.AsyncClient]:
    """取得 URL を引くための、トークンを付けない HTTP クライアント（同じ DB の別のアプリ）。"""
    app = _app(w)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
        ) as http,
    ):
        yield http


async def _pull(w: World, c: Client, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """knowledge_pull の応答と、その URL から取得した計画。"""
    ticket = _json(await c.call_tool("knowledge_pull", args))
    async with _http(w) as http:
        res = await http.get(urlsplit(ticket["plan_url"]).path)
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    return ticket, res.json()


class _BridgeOpener:
    """実行スクリプトの urllib の取得を、テストのイベントループの ASGI アプリに渡す。"""

    def __init__(self, http: httpx2.AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self.http, self.loop = http, loop

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
        url = request.full_url
        res = asyncio.run_coroutine_threadsafe(self.http.get(urlsplit(url).path), self.loop).result(
            timeout=60
        )
        if res.status_code != 200:
            raise urllib.error.HTTPError(url, res.status_code, "error", hdrs=None, fp=None)  # type: ignore[arg-type]
        return io.BytesIO(res.content)


async def _apply_url(w: World, plan_url: str, repo: Path, *extra: str) -> dict[str, Any]:
    """`speclino_apply.py --plan-url` を実行する（取得は ASGI アプリに橋渡し）。"""
    loop = asyncio.get_running_loop()
    async with _http(w) as http:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                sync_apply.urllib.request, "build_opener", lambda *a: _BridgeOpener(http, loop)
            )
            out = io.StringIO()
            mp.setattr(sys, "stdout", out)
            argv = ["--plan-url", plan_url, "--allow-http", "--repo", str(repo), *extra]
            code = await asyncio.to_thread(sync_apply.main, argv)
    result: dict[str, Any] = json.loads(out.getvalue().splitlines()[-1])
    assert result["exit_code"] == code
    return result


async def test_knowledge_pull_returns_url_and_round_trip(world: World) -> None:
    """URL 方式の往復: 取り込み → VERIFIED → 改訂の承認 → 取り込み → VERIFIED →
    スキルを外す → 削除されて VERIFIED。計画の本文は MCP の応答に載らない。"""
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# 手書き\n")
    async with _client(w, token) as c:
        status = _json(await c.call_tool("sync_status", {"repo_full_name": w.repo}))
        assert status["pull_needed"] is True and status["reasons"] == ["no_lock"]
        ticket = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo}))
    assert set(ticket) == {"plan_url", "expires_at", "bundle_generation", "layout", "summary"}
    assert ticket["layout"] == {"instructions_file": "AGENTS.md", "skills_dir": ".agents/skills"}
    assert ticket["plan_url"].startswith("http://localhost:8080/api/sync/plans/slplan_")
    assert ticket["summary"]["ops"] == 5 and ticket["summary"]["deletes"] == 0
    assert await _fetch_channels(w) == []  # 取得したときに記録する
    gen = str(ticket["bundle_generation"])
    out = await _apply_url(w, ticket["plan_url"], repo, "--expect-generation", gen)
    assert out["ok"] is True, out
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"
    assert await _fetch_channels(w) == ["sync"]
    # 同じ URL は 2 回目は使えない
    again = await _apply_url(w, ticket["plan_url"], repo)
    assert again["exit_code"] == 2 and "404" in again["reason"]

    lock_json = (repo / ks.LOCK_PATH).read_text()
    async with _client(w, token) as c:
        fresh = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
    assert fresh["pull_needed"] is False and fresh["reasons"] == []

    await _revise(w, "review", _skill("新しい手順", ArtifactScope(projects=[w.pid])))
    assert await _check(w, repo, w.pid, w.repo) == "REVIEW"
    async with _client(w, token) as c:
        ticket = _json(
            await c.call_tool("knowledge_pull", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
    assert (await _apply_url(w, ticket["plan_url"], repo))["ok"] is True
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"

    pid_b = str(await project_id_of(w.engine, w.repo2))
    await _revise(w, "review", _skill("新しい手順", ArtifactScope(projects=[pid_b])))
    lock_json = (repo / ks.LOCK_PATH).read_text()
    async with _client(w, token) as c:
        ticket = _json(
            await c.call_tool("knowledge_pull", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
    assert ticket["summary"]["deletes"] == 2
    out = await _apply_url(w, ticket["plan_url"], repo)
    assert out["ok"] is True and ".agents/skills/review/SKILL.md" in out["deleted"]
    assert not (repo / ".agents/skills/review").exists()
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"
    assert (repo / "AGENTS.md").read_text().startswith("# 手書き\n")


async def test_plan_url_expires_and_is_bound_to_the_repository(world: World) -> None:
    w = world
    await _seed(w)
    async with w.f() as s:
        project = await s.get(Project, uuid.UUID(w.pid))
    assert project is not None
    past = datetime.now(UTC) - timedelta(minutes=11)
    async with w.f() as s, s.begin():
        old = await ks.issue_plan_ticket(
            s,
            workspace=w.slug,
            project=project,
            lock_json=None,
            channel="mcp",
            token_id=None,
            now=past,
        )
        moved = await ks.issue_plan_ticket(
            s, workspace=w.slug, project=project, lock_json=None, channel="mcp", token_id=None
        )
    async with w.engine.begin() as conn:
        stored = (
            await conn.execute(
                text("SELECT token_hash, plan_json FROM sync_plans WHERE workspace = :w"),
                {"w": w.slug},
            )
        ).all()
    assert all(old.token not in r.token_hash and moved.token not in r.token_hash for r in stored)
    # リポジトリ ID が変わった（付け替えられた）プロジェクトの計画は渡さない
    async with w.engine.begin() as conn:
        await conn.execute(
            text("UPDATE projects SET github_repo_id = github_repo_id + 100000 WHERE id = :p"),
            {"p": w.pid},
        )
    async with _http(w) as http:
        expired = await http.get(f"/api/sync/plans/{old.token}")
        rebound = await http.get(f"/api/sync/plans/{moved.token}")
        unknown = await http.get("/api/sync/plans/slplan_nope")
    async with w.engine.begin() as conn:
        await conn.execute(
            text("UPDATE projects SET github_repo_id = github_repo_id - 100000 WHERE id = :p"),
            {"p": w.pid},
        )
    assert expired.status_code == rebound.status_code == unknown.status_code == 404
    assert await _fetch_channels(w) == []


async def test_knowledge_pull_deletes_only_with_verified_lock(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        _, first = await _pull(w, c, {"repo_full_name": w.repo})
    assert sync_apply.apply_plan(repo, first).ok
    lock_json = (repo / ks.LOCK_PATH).read_text()
    pid_b = str(await project_id_of(w.engine, w.repo2))
    await _revise(w, "review", _skill("手順", ArtifactScope(projects=[pid_b])))
    forged = json.loads(lock_json)
    forged["signature"] = "hmac-sha256:" + "0" * 64
    async with _client(w, token) as c:
        status = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
        _, no_delete = await _pull(
            w, c, {"repo_full_name": w.repo, "lock_json": json.dumps(forged)}
        )
        _, plan = await _pull(w, c, {"repo_full_name": w.repo, "lock_json": lock_json})
    assert ks.verify_plan(SyncPlan.model_validate(plan))
    assert status["pull_needed"] is True and status["reasons"] == ["members_changed"]
    assert {op["op"] for op in no_delete["ops"]} <= {
        "write",
        "set_managed_block",
        "remove_managed_block",  # 選ばれなかった側の片付け（期待ハッシュ無し）
    }
    assert all(
        op.get("expected_inner_sha256") is None
        for op in no_delete["ops"]
        if op["op"] == "remove_managed_block"
    )
    deletes = sorted(op["path"] for op in plan["ops"] if op["op"] == "delete")
    assert ".agents/skills/review/SKILL.md" in deletes and ".agents/skills/review/ref.md" in deletes
    assert plan["lock"]["absent"] == deletes
    assert sync_apply.apply_plan(repo, plan).ok
    assert not (repo / ".claude/skills/review").exists()
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"


async def test_knowledge_pull_tenant_isolation(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with _client(w, token) as c:
        for repo in (w.repo2, w.other_repo):
            hidden = await c.call_tool("knowledge_pull", {"repo_full_name": repo})
            assert hidden.is_error and "登録されていない" in _text(hidden)
            hidden = await c.call_tool("sync_status", {"repo_full_name": repo})
            assert hidden.is_error and "登録されていない" in _text(hidden)
    # ワークスペースの MCP トークンは同じワークスペースの別のリポジトリも読める（A9）
    mcp_token = await issue_mcp_token(w.engine, w.slug)
    async with _client(w, mcp_token) as c:
        _, plan_b = await _pull(w, c, {"repo_full_name": w.repo2})
        other = await c.call_tool("knowledge_pull", {"repo_full_name": w.other_repo})
    assert plan_b["github_repo_id"] == repo_id_of(w.repo2)
    assert plan_b["lock"]["github_repo_id"] == repo_id_of(w.repo2)
    assert other.is_error
    assert await _fetch_channels(w) == ["mcp"]


async def test_knowledge_pull_refuses_large_plan_and_inputs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with _client(w, token) as c:
        huge_lock = await c.call_tool(
            "knowledge_pull", {"repo_full_name": w.repo, "lock_json": "x" * (1024 * 1024 + 1)}
        )
        many = await c.call_tool(
            "sync_status",
            {"repo_full_name": w.repo, "local_hashes": {f"p{i}": "h" for i in range(2001)}},
        )
        monkeypatch.setattr(sync_apply, "MAX_PLAN_BYTES", 100)
        refused = await c.call_tool("knowledge_pull", {"repo_full_name": w.repo})
    assert huge_lock.is_error and "lock_json" in _text(huge_lock)
    assert many.is_error and "local_hashes" in _text(many)
    assert refused.is_error and "超える" in _text(refused)
    assert await _fetch_channels(w) == []


async def test_sync_status_reports_modified_files(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        _, plan = await _pull(w, c, {"repo_full_name": w.repo})
    assert sync_apply.apply_plan(repo, plan).ok
    lock = json.loads((repo / ks.LOCK_PATH).read_text())
    hashes = {p: h for p, h in lock["files"].items() if p not in ks.MANAGED_FILES}
    inner = {p: h for p, h in lock["files"].items() if p in ks.MANAGED_FILES}
    args = {"repo_full_name": w.repo, "lock_json": json.dumps(lock)}
    tampered = {**hashes, ".agents/skills/review/SKILL.md": ks.file_hash("改ざん")}
    plain_hex = {p: h.removeprefix("sha256:") for p, h in hashes.items()}
    async with _client(w, token) as c:
        ok = _json(
            await c.call_tool(
                "sync_status", {**args, "local_hashes": plain_hex, "managed_inner": inner}
            )
        )
        bad = _json(await c.call_tool("sync_status", {**args, "local_hashes": tampered}))
        missing = _json(
            await c.call_tool(
                "sync_status", {**args, "managed_inner": {"AGENTS.md": ks.file_hash("x")}}
            )
        )
    assert ok["pull_needed"] is False
    assert bad["pull_needed"] is True and bad["reasons"] == ["files_modified"]
    assert bad["modified_files"] == [".agents/skills/review/SKILL.md"]
    assert missing["modified_files"] == ["AGENTS.md"]


@pytest.mark.parametrize("kind", ["sync", "mcp"])
async def test_plan_url_of_revoked_token_is_404(world: World, kind: str) -> None:
    """fix round 2: 発行元のトークンを取り消したら、取り消し前の計画も渡さず記録もしない。"""
    w = world
    await _seed(w)
    token = await _sync_token(w) if kind == "sync" else await issue_mcp_token(w.engine, w.slug)
    async with _client(w, token) as c:
        ticket = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo}))
    table = "sync_tokens" if kind == "sync" else "mcp_tokens"
    async with w.engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE {table} SET revoked_at = now() WHERE token_hash = :h"),
            {"h": hashlib.sha256(token.encode()).hexdigest()},
        )
    async with _http(w) as http:
        res = await http.get(urlsplit(ticket["plan_url"]).path)
    assert res.status_code == 404
    assert await _fetch_channels(w) == []


async def test_plan_body_is_cleared_once_fetched(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    async with _client(w, token) as c:
        _, plan = await _pull(w, c, {"repo_full_name": w.repo})
    assert plan["ops"]
    async with w.engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT plan_json, consumed_at FROM sync_plans WHERE workspace = :w"),
                {"w": w.slug},
            )
        ).all()
    assert [(r.plan_json, r.consumed_at is not None) for r in rows] == [("", True)]


async def test_expired_plans_are_purged_when_fetching(world: World) -> None:
    w = world
    await _seed(w)
    async with w.f() as s:
        project = await s.get(Project, uuid.UUID(w.pid))
    assert project is not None
    long_ago = datetime.now(UTC) - timedelta(days=2)
    async with w.f() as s, s.begin():
        await ks.issue_plan_ticket(
            s,
            workspace=w.slug,
            project=project,
            lock_json=None,
            channel="mcp",
            token_id=None,
            now=long_ago,
        )
    async with w.f() as s, s.begin():
        assert await ks.redeem_plan_ticket(s, "slplan_" + "a" * 43) is None
    async with w.engine.connect() as conn:
        left = (
            await conn.execute(
                text("SELECT count(*) FROM sync_plans WHERE workspace = :w"), {"w": w.slug}
            )
        ).scalar_one()
    assert left == 0


async def test_knowledge_pull_layout_params(world: World) -> None:
    """S76: 配置は取り込む側が選ぶ。省略は今の配置を保ち、列挙の外は拒否する。"""
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    claude = {"instructions_file": "CLAUDE.md", "skills_dir": ".claude/skills"}
    async with _client(w, token) as c:
        ticket, plan = await _pull(w, c, {"repo_full_name": w.repo, **claude})
        assert ticket["layout"] == claude
        assert sync_apply.apply_plan(repo, plan).ok
        assert (repo / "CLAUDE.md").exists() and not (repo / "AGENTS.md").exists()
        assert (repo / ".claude/skills/review/SKILL.md").exists()
        assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"
        lock_json = (repo / ks.LOCK_PATH).read_text()
        # 省略すると今の配置（claude）を保つ
        kept, _ = await _pull(w, c, {"repo_full_name": w.repo, "lock_json": lock_json})
        assert kept["layout"] == claude
        # 配置を変えるときだけ指定する。前の配置のパスは消える
        agents = {"instructions_file": "AGENTS.md", "skills_dir": ".agents/skills"}
        moved, switch = await _pull(
            w, c, {"repo_full_name": w.repo, "lock_json": lock_json, **agents}
        )
        assert moved["layout"] == agents
        bad_file = await c.call_tool(
            "knowledge_pull", {"repo_full_name": w.repo, "instructions_file": "README.md"}
        )
        bad_dir = await c.call_tool(
            "knowledge_pull", {"repo_full_name": w.repo, "skills_dir": ".github/skills"}
        )
    assert bad_file.is_error and "instructions_file" in _text(bad_file)
    assert bad_dir.is_error and "skills_dir" in _text(bad_dir)
    assert sync_apply.apply_plan(repo, switch).ok
    assert "speclino:begin" in (repo / "AGENTS.md").read_text()
    assert "speclino:begin" not in (repo / "CLAUDE.md").read_text()  # 区画だけが消える
    assert not (repo / ".claude/skills/review").exists()
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"


async def test_sync_status_reports_stale_copy(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        _, plan = await _pull(w, c, {"repo_full_name": w.repo})
        assert sync_apply.apply_plan(repo, plan).ok
        lock = json.loads((repo / ks.LOCK_PATH).read_text())
        args = {"repo_full_name": w.repo, "lock_json": json.dumps(lock)}
        own = {p: h for p, h in lock["files"].items() if p not in ks.MANAGED_FILES}
        own_inner = {p: h for p, h in lock["files"].items() if p in ks.MANAGED_FILES}
        stale = _json(
            await c.call_tool(
                "sync_status",
                {
                    **args,
                    "local_hashes": {**own, ".claude/skills/review/SKILL.md": ks.file_hash("古")},
                    "managed_inner": {**own_inner, "CLAUDE.md": ks.file_hash("古")},
                },
            )
        )
        clean = _json(await c.call_tool("sync_status", args))
    assert stale["pull_needed"] is True and stale["reasons"] == ["stale_copy"]
    assert stale["stale_copies"] == [".claude/skills/review/", "CLAUDE.md"]
    assert clean["pull_needed"] is False and clean["stale_copies"] == []


async def test_sync_status_layout_change_and_stale_verdict(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        _, plan = await _pull(w, c, {"repo_full_name": w.repo})
        assert sync_apply.apply_plan(repo, plan).ok
        lock = json.loads((repo / ks.LOCK_PATH).read_text())
        args = {"repo_full_name": w.repo, "lock_json": json.dumps(lock)}
        claude = {"instructions_file": "CLAUDE.md", "skills_dir": ".claude/skills"}
        same = _json(await c.call_tool("sync_status", {**args, "instructions_file": "AGENTS.md"}))
        changed = _json(await c.call_tool("sync_status", {**args, **claude}))
        bad = await c.call_tool("sync_status", {**args, "skills_dir": "other/skills"})
        own = {p: h for p, h in lock["files"].items() if p not in ks.MANAGED_FILES}
        inner = {p: h for p, h in lock["files"].items() if p in ks.MANAGED_FILES}
        stale = _json(
            await c.call_tool(
                "sync_status",
                {**args, "local_hashes": own, "managed_inner": {**inner, "CLAUDE.md": "x"}},
            )
        )
    assert same["pull_needed"] is False
    assert changed["pull_needed"] is True and changed["reasons"] == ["layout_change"]
    assert bad.is_error and "skills_dir" in _text(bad)
    assert stale["verdict"] == "REVIEW" and stale["stale"] is True
    assert "古い写し" in stale["reason"]
