"""MCP の `spec_draft_save`: エディタのエージェントが仕様の下書きを保存する（承認はしない）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from speclino.db.repositories.mcp_tokens import McpTokenRepository
from speclino.domain.knowledge import ArtifactScope, SpecContent
from speclino.services import knowledge_registry as kr
from speclino.services import mcp_tokens
from tests.integration.services.test_knowledge_registry import _spec
from tests.integration.services.test_mcp_knowledge import (
    World,
    _approve,
    _client,
    _json,
    _sync_token,
    _text,
    world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

__all__ = ["world"]

MD = "# 注文\n\n本文\n"


async def _mcp_token(w: World, created_by: str = "owner1") -> str:
    token = mcp_tokens.TOKEN_PREFIX + uuid.uuid4().hex
    async with w.f() as s, s.begin():
        await McpTokenRepository(s).create(
            team=w.slug,
            name="agent",
            token_hash=mcp_tokens.hash_token(token),
            created_by=created_by,
        )
    return token


async def test_new_spec_is_saved_as_draft_by_token_issuer(world: World) -> None:
    w = world
    token = await _mcp_token(w, created_by="member1")
    async with _client(w, token) as c:
        saved = _json(
            await c.call_tool(
                "spec_draft_save",
                {"repo_full_name": w.repo, "name": "orders", "markdown": MD, "note": "注文を足す"},
            )
        )
    assert saved["revision"] == 1 and saved["status"] == "draft"
    assert isinstance(saved["errors"], int) and isinstance(saved["diagnostics"], list)
    assert saved["editor_url"].endswith(f"/w/{w.slug}/knowledge/specs/{saved['artifact_id']}")
    async with w.f() as s:
        draft = await kr.current_draft(s, saved["artifact_id"])
        bundle = await kr.current_bundle(s, w.slug)
    assert draft is not None and draft.author == "member1" and draft.note == "注文を足す"
    assert isinstance(draft.content, SpecContent)
    assert draft.content.scope == ArtifactScope(projects=[w.pid])
    assert bundle is None  # 承認はしない
    async with w.engine.connect() as conn:
        detail = (
            await conn.execute(
                text(
                    "SELECT detail FROM audit_events WHERE action = 'mcp.spec_draft_save'"
                    " AND target LIKE :t"
                ),
                {"t": f"{w.slug}/{saved['artifact_id']}@%"},
            )
        ).scalar_one()
    assert detail["name"] == "orders" and detail["token_id"]


async def test_existing_spec_requires_the_revision_the_agent_read(world: World) -> None:
    w = world
    await _approve(w, "spec", "orders", _spec(ArtifactScope(projects=[w.pid]), "R-ORD-1"))
    token = await _mcp_token(w)
    args = {"repo_full_name": w.repo, "name": "orders", "markdown": MD}
    async with _client(w, token) as c:
        active = _json(await c.call_tool("spec_get", {"repo_full_name": w.repo, "name": "orders"}))
        no_base = await c.call_tool("spec_draft_save", args)
        first = _json(
            await c.call_tool(
                "spec_draft_save",
                {
                    **args,
                    "expected_revision": active["revision"],
                    "expected_hash": active["content_hash"],
                },
            )
        )
        # 下書きができた後に、古い改訂を前提にした保存は上書きしない
        stale = await c.call_tool(
            "spec_draft_save",
            {
                **args,
                "markdown": "# 古い\n",
                "expected_revision": active["revision"],
                "expected_hash": active["content_hash"],
            },
        )
        second = _json(
            await c.call_tool(
                "spec_draft_save",
                {
                    **args,
                    "markdown": "# 注文 v2\n",
                    "expected_revision": first["revision"],
                    "expected_hash": first["content_hash"],
                },
            )
        )
    assert no_base.is_error and "content_hash" in _text(no_base)
    assert first["revision"] == 2 and first["status"] == "draft"
    assert stale.is_error and "今の改訂は 2" in _text(stale)
    assert first["content_hash"] not in _text(stale)  # 読んでいない下書きの hash は返さない
    assert second["revision"] == 2 and second["content_hash"] != first["content_hash"]
    async with w.f() as s:
        draft = await kr.current_draft(s, first["artifact_id"])
    assert draft is not None and isinstance(draft.content, SpecContent)
    assert draft.content.markdown == "# 注文 v2\n"
    assert draft.content.scope == ArtifactScope(projects=[w.pid])  # scope は引き継ぐ


async def test_refuses_sync_token_non_member_issuer_and_other_repos(world: World) -> None:
    w = world
    await _approve(w, "spec", "orders", _spec(ArtifactScope(projects=[w.pid]), "R-ORD-1"))
    args = {"name": "fresh", "markdown": MD}
    async with _client(w, await _sync_token(w)) as c:
        sync = await c.call_tool("spec_draft_save", {**args, "repo_full_name": w.repo})
    async with _client(w, await _mcp_token(w, created_by="left-the-team")) as c:
        gone = await c.call_tool("spec_draft_save", {**args, "repo_full_name": w.repo})
    async with _client(w, await _mcp_token(w)) as c:
        other_ws = await c.call_tool("spec_draft_save", {**args, "repo_full_name": w.other_repo})
        # orders は a のプロジェクトだけに当たる。b からは「無い」と同じ
        not_here = await c.call_tool(
            "spec_draft_save",
            {
                "repo_full_name": w.repo2,
                "name": "orders",
                "markdown": MD,
                "expected_revision": 1,
                "expected_hash": "x",
            },
        )
        bad_name = await c.call_tool(
            "spec_draft_save", {**args, "name": "../x", "repo_full_name": w.repo}
        )
    assert sync.is_error and "読み取り専用" in _text(sync)
    assert gone.is_error and "保存できない" in _text(gone)
    assert other_ws.is_error and "登録されていない" in _text(other_ws)
    assert not_here.is_error and "当たる仕様に無い" in _text(not_here)
    assert bad_name.is_error
    async with w.f() as s:
        names = {a.name for a in await kr.list_artifacts(s, w.slug)}
    assert names == {"orders"}
