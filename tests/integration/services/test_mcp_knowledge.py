"""S68・S75: MCP の知識ツール（sync_status / knowledge_pull / spec_get / skill_get）と
sync トークンの制限。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

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
from speclino.domain.knowledge import ArtifactScope, InstructionsContent, SkillContent
from speclino.domain.knowledge_sync import SyncIdentity, SyncPlan
from speclino.mcp_server import server as mcp_server
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
            target="agents_md",
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


async def test_knowledge_pull_plan_applies_and_verifies(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        status = _json(await c.call_tool("sync_status", {"repo_full_name": w.repo}))
        assert status["pull_needed"] is True and status["reasons"] == ["no_lock"]
        plan = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo}))
    assert ks.verify_plan(SyncPlan.model_validate(plan))
    assert plan["repo_full_name"] == w.repo and plan["github_repo_id"] == repo_id_of(w.repo)
    assert sync_apply.apply_plan(repo, plan).ok
    assert await _check(w, repo, w.pid, w.repo) == "VERIFIED_IN_SCOPE"
    assert await _fetch_channels(w) == ["sync"]
    lock_json = (repo / ks.LOCK_PATH).read_text()
    async with _client(w, token) as c:
        fresh = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
    assert fresh["pull_needed"] is False and fresh["reasons"] == []


async def test_knowledge_pull_deletes_only_with_verified_lock(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        first = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo}))
    assert sync_apply.apply_plan(repo, first).ok
    lock_json = (repo / ks.LOCK_PATH).read_text()
    # スキルを同じワークスペースの別のプロジェクトに移す（このリポジトリからは外れる）
    pid_b = str(await project_id_of(w.engine, w.repo2))
    await _revise(w, "review", _skill("手順", ArtifactScope(projects=[pid_b])))
    forged = json.loads(lock_json)
    forged["signature"] = "hmac-sha256:" + "0" * 64
    async with _client(w, token) as c:
        status = _json(
            await c.call_tool("sync_status", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
        no_delete = _json(
            await c.call_tool(
                "knowledge_pull", {"repo_full_name": w.repo, "lock_json": json.dumps(forged)}
            )
        )
        plan = _json(
            await c.call_tool("knowledge_pull", {"repo_full_name": w.repo, "lock_json": lock_json})
        )
    assert status["pull_needed"] is True and status["reasons"] == ["members_changed"]
    assert {op["op"] for op in no_delete["ops"]} <= {"write", "set_managed_block"}
    deletes = sorted(op["path"] for op in plan["ops"] if op["op"] == "delete")
    assert ".claude/skills/review/SKILL.md" in deletes and ".agents/skills/review/ref.md" in deletes
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
        plan_b = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo2}))
        other = await c.call_tool("knowledge_pull", {"repo_full_name": w.other_repo})
    assert plan_b["github_repo_id"] == repo_id_of(w.repo2)
    assert plan_b["lock"]["github_repo_id"] == repo_id_of(w.repo2)
    assert other.is_error
    assert await _fetch_channels(w) == ["mcp"]


async def test_knowledge_pull_refuses_large_plan(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    monkeypatch.setattr(mcp_server, "MCP_PLAN_MAX_BYTES", 100)
    async with _client(w, token) as c:
        refused = await c.call_tool("knowledge_pull", {"repo_full_name": w.repo})
    assert refused.is_error and "speclino sync" in _text(refused)
    assert await _fetch_channels(w) == []


async def test_sync_status_reports_modified_files(world: World) -> None:
    w = world
    await _seed(w)
    token = await _sync_token(w)
    repo = w.tmp / "checkout"
    repo.mkdir()
    async with _client(w, token) as c:
        plan = _json(await c.call_tool("knowledge_pull", {"repo_full_name": w.repo}))
    assert sync_apply.apply_plan(repo, plan).ok
    lock = json.loads((repo / ks.LOCK_PATH).read_text())
    hashes = {p: h for p, h in lock["files"].items() if p not in ks.MANAGED_FILES}
    inner = {p: h for p, h in lock["files"].items() if p in ks.MANAGED_FILES}
    args = {"repo_full_name": w.repo, "lock_json": json.dumps(lock)}
    tampered = {**hashes, ".claude/skills/review/SKILL.md": ks.file_hash("改ざん")}
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
    assert bad["modified_files"] == [".claude/skills/review/SKILL.md"]
    assert missing["modified_files"] == ["AGENTS.md"]
