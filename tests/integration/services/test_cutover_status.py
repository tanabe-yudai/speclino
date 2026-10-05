"""切り替えの進み具合（#114、services/cutover_status.py）。実 PostgreSQL が必要。"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.domain.knowledge import ArtifactScope, SpecContent
from speclino.services import knowledge_registry as kr
from speclino.services import sync_tokens
from speclino.services.cutover_status import cutover_status
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID
from tests.integration.services.conftest import project_id_of, register_repo
from tests.integration.services.test_observability import _add_run, _finding
from tests.unit.services.test_check_pipeline_bundle import DOMAIN, UML

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

NOW = datetime.now(UTC)


async def test_cutover_status_per_project(engine: AsyncEngine) -> None:
    slug = f"e2e-cut-{uuid.uuid4().hex[:8]}"
    done_repo, todo_repo = f"e2e/{slug}-done", f"e2e/{slug}-todo"
    members = {"owner1": "owner", "member1": "member"}
    await register_repo(engine, done_repo, members, team=slug)
    await register_repo(engine, todo_repo, members, team=slug)
    done, todo = await project_id_of(engine, done_repo), await project_id_of(engine, todo_repo)
    f = async_sessionmaker(engine, expire_on_commit=False)

    async with f() as s:
        before = await cutover_status(s, slug)
    assert [(r.repo_full_name, r.ready, r.bundle_generation) for r in before] == [
        (done_repo, False, None),
        (todo_repo, False, None),
    ]
    assert all(r.bundle_problem for r in before)  # bundle がまだ無い

    # done: 仕様を承認 → sync トークン → lock の一致した検査
    async with f() as s, s.begin():
        draft = await kr.create_draft(
            s,
            workspace=slug,
            kind="spec",
            name="orders",
            content=SpecContent(
                kind="spec", markdown=UML, domain=DOMAIN, scope=ArtifactScope(projects=[str(done)])
            ),
            author="member1",
        )
    async with f() as s, s.begin():
        await kr.approve(
            s,
            artifact_id=draft.artifact_id,
            expected_revision=draft.revision,
            expected_hash=draft.content_hash,
            approver="owner1",
        )
        await sync_tokens.issue(s, slug=slug, project_id=done, name="ci", actor_uid="owner1")
        # 古い run は lock が無い（UNKNOWN）、新しい run で一致した
        await _add_run(
            s, done, NOW - timedelta(hours=2), [_finding(LOCK_REQUIREMENT_ID, "k", "UNKNOWN")]
        )
        latest = await _add_run(
            s, done, NOW, [_finding(LOCK_REQUIREMENT_ID, "k", "VERIFIED_IN_SCOPE")]
        )
        # todo: 失効したトークンだけ。検査は lock が古い
        issued = await sync_tokens.issue(
            s, slug=slug, project_id=todo, name="old", actor_uid="owner1"
        )
        await sync_tokens.revoke(s, slug=slug, token_id=issued.info.id, actor_uid="owner1")
        await _add_run(s, todo, NOW, [_finding(LOCK_REQUIREMENT_ID, "k", "REVIEW")])

    async with f() as s:
        rows = {r.repo_full_name: r for r in await cutover_status(s, slug)}
        everything = await cutover_status(s)
    ok, ng = rows[done_repo], rows[todo_repo]
    assert ok.ready and ok.approved_specs == ["orders"] and ok.active_sync_tokens == 1
    assert (ok.latest_run_id, ok.lock_verdict) == (str(latest), "VERIFIED_IN_SCOPE")
    assert ok.bundle_generation == 1 and ok.bundle_problem is None
    # 仕様のスコープ外・失効したトークン・古い lock は ready にしない
    assert not ng.ready and ng.approved_specs == [] and ng.active_sync_tokens == 0
    assert ng.lock_verdict == "REVIEW"
    # ワークスペースを省略すると全部（このワークスペースの分も入る）
    assert {done_repo, todo_repo} <= {r.repo_full_name for r in everything}


async def test_cli_prints_project_id(engine: AsyncEngine) -> None:
    """手順書は cutover-status の `project=<ID>` で import-specs の project ID を確かめる。"""
    import asyncio

    from typer.testing import CliRunner

    from speclino.cli import app

    slug = f"e2e-cutcli-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = await project_id_of(engine, repo)
    # CLI は自分で asyncio.run するので別のスレッドで動かす
    result = await asyncio.to_thread(
        CliRunner().invoke, app, ["cutover-status", "--workspace", slug]
    )
    assert result.exit_code == 0, result.output
    assert f"project={pid}" in result.output
