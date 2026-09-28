"""MCP（S33）: エディタのエージェントが context_get → verification_start → verification_get を
Streamable HTTP + Bearer で実行できる（デモ 6 の前段: 仕様を書いてその場で検証する）。

S54: トークンはワークスペース単位で、そのワークスペースのリポジトリと run にだけ触れる。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState, make_team_policy_resolver
from speclino.db.repositories.mcp_tokens import McpTokenRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.services import mcp_tokens
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.db.test_policy_revisions_repository import _package
from tests.integration.services.conftest import E2E_TEAM, project_id_of, register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


async def _issue(engine: AsyncEngine, team: str) -> str:
    """team の MCP トークンを発行する（発行 API の認可は test_mcp_tokens_api で見る）。"""
    token = mcp_tokens.TOKEN_PREFIX + uuid.uuid4().hex
    async with async_sessionmaker(engine)() as session, session.begin():
        await McpTokenRepository(session).create(
            team=team, name="test", token_hash=mcp_tokens.hash_token(token), created_by="t"
        )
    return token


def _text(result) -> dict:  # type: ignore[no-untyped-def]
    """ツール結果の最初のテキスト内容を JSON として読む。"""
    block = result.content[0]
    return json.loads(block.text)


async def test_context_start_get_over_mcp(engine: AsyncEngine, tmp_path: Path) -> None:
    repo = f"e2e/mcp-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    token = await _issue(engine, E2E_TEAM)
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", code_host_kind="local")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service)
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http,
    ):
        transport = streamable_http_client("http://localhost/mcp", http_client=http)
        async with Client(transport) as client:
            # S53: ワークスペースに登録されていないリポジトリは読めない・検証を始められない
            for tool, args in (
                ("context_get", {"repo_full_name": "e2e/unregistered", "ref": base}),
                (
                    "verification_start",
                    {"repo_full_name": "e2e/unregistered", "head_sha": head},
                ),
            ):
                refused = await client.call_tool(tool, args)
                assert refused.is_error and "登録されていない" in refused.content[0].text
            # 名前で取れるリポジトリの ID が登録時と違えば（旧名の再利用）読まない
            host.repo_ids[repo] = repo_id_of(repo) + 1
            swapped = await client.call_tool("context_get", {"repo_full_name": repo, "ref": base})
            assert swapped.is_error and "リポジトリ ID が登録時と違う" in swapped.content[0].text
            host.repo_ids[repo] = repo_id_of(repo)
            ctx = _text(
                await client.call_tool("context_get", {"repo_full_name": repo, "ref": base})
            )
            assert "order.status" in ctx["domain"]["variables"]
            assert {r["id"] for r in ctx["rules"]} >= {"R-CANCEL-04"}
            started = _text(
                await client.call_tool(
                    "verification_start",
                    {"repo_full_name": repo, "head_sha": head, "base_sha": base},
                )
            )
            got = _text(await client.call_tool("verification_get", {"run_id": started["run_id"]}))
            # 登録を外すと、同じ run でも読めない（無い場合と同じ応答）
            async with engine.begin() as conn:
                await conn.execute(
                    sql_text("UPDATE projects SET team = NULL WHERE repo_full_name = :r"),
                    {"r": repo},
                )
            hidden = _text(
                await client.call_tool("verification_get", {"run_id": started["run_id"]})
            )
            assert hidden == {"error": "run_not_found", "run_id": started["run_id"]}
    assert got["exec_state"] == "completed" and got["gate_status"] == "fail", got
    assert got["semantic_diff"]["counts"]["new_violation"] == 1
    violated = [f for f in got["findings"] if f["verdict"] == "VIOLATED"]
    assert [f["requirement_id"] for f in violated] == ["R-CANCEL-04"]
    assert "next_steps" in violated[0] or violated[0]["category"] == "missing_effect"


async def test_context_get_serves_the_active_team_policy(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """S44: MCP の context_get がチームの active 改訂を返し、承認で改訂が変わると内容も変わる。"""
    owner = f"e2e-team-{uuid.uuid4().hex[:8]}"
    repo = f"{owner}/demo"
    await register_repo(engine, repo, {}, team=owner)
    token = await _issue(engine, owner)
    base, _, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type, return-value]
    settings = Settings(env="local", role="all", code_host_kind="local")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    config = ReviewRunConfig(
        agent_enabled=False, resolve_team_policy=make_team_policy_resolver(factory)
    )
    service = ReviewRunService(factory, host, queue, blobs, config)
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))

    async def approve_revision(rule_id: str) -> None:
        async with factory() as session, session.begin():
            revisions = PolicyRevisionRepository(session)
            draft = await revisions.create_draft(
                team=owner, package=_package(rule_id, owner=owner), author="alice"
            )
            await revisions.approve(team=owner, revision=draft.revision, approver="bob")

    async def read_policies(client: Client) -> dict:  # type: ignore[type-arg]
        return _text(await client.call_tool("context_get", {"repo_full_name": repo, "ref": base}))

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http,
    ):
        transport = streamable_http_client("http://localhost/mcp", http_client=http)
        async with Client(transport) as client:
            assert (await read_policies(client))["team_policy"] is None
            await approve_revision("TEAM-A")
            first = await read_policies(client)
            await approve_revision("TEAM-B")
            second = await read_policies(client)

    assert first["team_policy"] == {"team": owner, "revision": 1}
    assert second["team_policy"] == {"team": owner, "revision": 2}
    team_ids = [
        [p["id"] for p in ctx["policies"] if p["origin"] == "team"] for ctx in (first, second)
    ]
    assert team_ids == [["TEAM-A"], ["TEAM-B"]]


async def test_a_token_reaches_only_its_own_workspace_and_stops_when_revoked(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """S54: A のトークンで B のリポジトリ・run に触れない。失効後は 401。

    使うと最終利用時刻が入る。
    """
    repo_a = f"e2e/mcp-a-{uuid.uuid4().hex[:8]}"
    repo_b = f"e2e/mcp-b-{uuid.uuid4().hex[:8]}"
    team_b = f"e2e-mcp-b-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo_a, {})
    await register_repo(engine, repo_b, {}, team=team_b)
    token = await _issue(engine, E2E_TEAM)
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(
        repos={repo_a: tmp_path / "repo", repo_b: tmp_path / "repo"},
        repo_ids={repo_a: repo_id_of(repo_a), repo_b: repo_id_of(repo_b)},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", code_host_kind="local")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    run_b = await service.create_run(
        project_id=await project_id_of(engine, repo_b),
        head_sha=head,
        base_sha=base,
        pr_number=None,
        trigger="test",
    )
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http,
    ):
        async with Client(streamable_http_client("http://localhost/mcp", http_client=http)) as c:
            assert "domain" in _text(
                await c.call_tool("context_get", {"repo_full_name": repo_a, "ref": base})
            )
            traversal = await c.call_tool(
                "context_get",
                {"repo_full_name": repo_a, "ref": f"../../../{repo_b}/tarball/main"},
            )
            assert traversal.is_error
            for tool, args in (
                ("context_get", {"repo_full_name": repo_b, "ref": base}),
                ("verification_start", {"repo_full_name": repo_b, "head_sha": head}),
            ):
                refused = await c.call_tool(tool, args)
                assert refused.is_error and "登録されていない" in refused.content[0].text
            other = _text(await c.call_tool("verification_get", {"run_id": str(run_b)}))
            assert other == {"error": "run_not_found", "run_id": str(run_b)}

        async with engine.begin() as conn:
            used = (
                await conn.execute(
                    sql_text("SELECT last_used_at FROM mcp_tokens WHERE token_hash = :h"),
                    {"h": mcp_tokens.hash_token(token)},
                )
            ).scalar_one()
            assert used is not None
            await conn.execute(
                sql_text("UPDATE mcp_tokens SET revoked_at = now() WHERE token_hash = :h"),
                {"h": mcp_tokens.hash_token(token)},
            )
        revoked = await http.post("/mcp", json={})
        assert revoked.status_code == 401


async def test_the_worker_builds_the_context_for_an_authorized_project(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """S54: web が委譲する worker の内部エンドポイント。ID が食い違えば 409（取得しない）。"""
    repo = f"e2e/mcp-worker-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {})
    base, _, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type, return-value]
    settings = Settings(env="local", role="worker", code_host_kind="local")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    project_id = str(await project_id_of(engine, repo))
    body = {"project_id": project_id, "workspace": E2E_TEAM, "ref": base}
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://w") as http,
    ):
        ok = await http.post("/internal/mcp/context", json=body)
        assert ok.status_code == 200 and "order.status" in ok.json()["domain"]["variables"]
        # web の確認の後に別のワークスペースへ移された project は返さない
        moved = await http.post("/internal/mcp/context", json={**body, "workspace": "e2e-else"})
        assert moved.status_code == 409
        # ref は URL のパスに入るので、`..` で別リポジトリへ移れる形は受け付けない
        bad_ref = await http.post("/internal/mcp/context", json={**body, "ref": "../../x/y"})
        assert bad_ref.status_code == 422
        host.repo_ids[repo] = repo_id_of(repo) + 1
        swapped = await http.post("/internal/mcp/context", json=body)
        assert swapped.status_code == 409
        assert "リポジトリ ID が登録時と違う" in swapped.json()["error"]["message"]
