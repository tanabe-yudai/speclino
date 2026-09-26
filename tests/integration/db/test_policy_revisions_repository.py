"""`PolicyRevisionRepository` / `services.policy_governance` の統合試験（S42）。

実 PostgreSQL が必要。`SPECLINO_DATABASE_URL` が未設定ならモジュール全体を skip する
（`tests/integration/db/test_repositories.py` と同じ理由・同じフィクスチャ構成）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from speclino.db.models import AuditEvent, Base
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.policy import PolicyApplicability, PolicyPackage, PolicyRule
from speclino.services import policy_governance

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _DATABASE_URL is None,
        reason="SPECLINO_DATABASE_URL が未設定のため DB 統合テストを skip する",
    ),
]


@pytest_asyncio.fixture(scope="session")
async def _schema_ready() -> None:
    assert _DATABASE_URL is not None
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(_schema_ready: None) -> AsyncIterator[AsyncSession]:
    """1トランザクションにテストを閉じ込め、終了時に必ず rollback する。"""
    assert _DATABASE_URL is not None
    engine = create_async_engine(_DATABASE_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session_factory = async_sessionmaker(bind=conn, expire_on_commit=False)
        async_session = session_factory()
        try:
            yield async_session
        finally:
            await async_session.close()
            await trans.rollback()
    await engine.dispose()


def _package(rule_id: str = "R-1", *, owner: str = "acme") -> PolicyPackage:
    return PolicyPackage(
        schema_version="0.1",
        id="test-package",
        version="1.0.0",
        status="active",
        owner=owner,
        rules=[
            PolicyRule(
                id=rule_id,
                version="1.0.0",
                level="advisory",
                title=f"title-{rule_id}",
                text=f"text-{rule_id}",
                applies_to=PolicyApplicability(paths=["**/*.py"], languages=[]),
                verifier=None,
                on_missing_input="skipped",
                exceptions_allowed=True,
            )
        ],
    )


async def test_create_draft_starts_at_revision_1_and_increments(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)

    first = await repo.create_draft(team="acme", package=_package(), author="alice")
    second = await repo.create_draft(team="acme", package=_package("R-2"), author="alice")

    assert (first.revision, second.revision) == (1, 2)
    assert first.status == "draft"
    assert second.status == "draft"


async def test_approve_activates_and_supersedes_previous_active(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    rev1 = await repo.create_draft(team="acme", package=_package(), author="alice")
    await repo.approve(team="acme", revision=rev1.revision, approver="bob")
    rev2 = await repo.create_draft(team="acme", package=_package("R-2"), author="alice")

    approved = await repo.approve(team="acme", revision=rev2.revision, approver="bob")

    assert approved.status == "active"
    assert approved.approver == "bob"
    previous = await repo.revision_at("acme", rev1.revision)
    assert previous is not None
    assert previous.status == "superseded"


async def test_approve_rejects_non_draft_revision(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    rev1 = await repo.create_draft(team="acme", package=_package(), author="alice")
    await repo.approve(team="acme", revision=rev1.revision, approver="bob")

    with pytest.raises(ValueError, match="draft でない"):
        await repo.approve(team="acme", revision=rev1.revision, approver="carol")


async def test_active_revision_returns_at_most_one(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    assert await repo.active_revision("acme") is None

    rev1 = await repo.create_draft(team="acme", package=_package(), author="alice")
    await repo.approve(team="acme", revision=rev1.revision, approver="bob")

    active = await repo.active_revision("acme")
    assert active is not None
    assert active.revision == rev1.revision
    assert active.status == "active"


async def test_revision_at_returns_past_revision_unchanged(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    rev1 = await repo.create_draft(team="acme", package=_package(), author="alice")
    await repo.approve(team="acme", revision=rev1.revision, approver="bob")
    rev2 = await repo.create_draft(team="acme", package=_package("R-2"), author="alice")
    await repo.approve(team="acme", revision=rev2.revision, approver="bob")

    frozen = await repo.revision_at("acme", rev1.revision)
    assert frozen is not None
    assert frozen.status == "superseded"
    assert frozen.package.rules[0].id == "R-1"


async def test_list_revisions_returns_newest_first(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    await repo.create_draft(team="acme", package=_package(), author="alice")
    await repo.create_draft(team="acme", package=_package("R-2"), author="alice")
    await repo.create_draft(team="acme", package=_package("R-3"), author="alice")

    revisions = await repo.list_revisions("acme")

    assert [r.revision for r in revisions] == [3, 2, 1]


async def test_teams_are_independent(session: AsyncSession) -> None:
    repo = PolicyRevisionRepository(session)
    acme = await repo.create_draft(team="acme", package=_package(), author="alice")
    other = await repo.create_draft(team="other", package=_package(), author="alice")

    assert acme.revision == 1
    assert other.revision == 1


async def test_create_draft_records_audit_event(session: AsyncSession) -> None:
    revision = await policy_governance.create_draft(
        session, team="acme", package=_package(), author="alice", note="最初の下書き"
    )

    rows = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.target == f"acme@{revision.revision}")
            )
        )
        .scalars()
        .all()
    )
    actions = {row.action for row in rows}
    assert "policy.draft" in actions


async def test_approve_records_audit_event(session: AsyncSession) -> None:
    revision = await policy_governance.create_draft(
        session, team="acme", package=_package(), author="alice"
    )
    await policy_governance.approve(
        session, team="acme", revision=revision.revision, approver="bob"
    )

    rows = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.target == f"acme@{revision.revision}")
            )
        )
        .scalars()
        .all()
    )
    actions = {row.action for row in rows}
    assert {"policy.draft", "policy.approve"} <= actions


async def test_seed_from_package_loads_speclino_core_as_revision_1(session: AsyncSession) -> None:
    source = REPO_ROOT / ".speclino" / "policy" / "speclino-core.yaml"
    package = PolicyPackage.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))

    revision = await policy_governance.seed_from_package(session, team="acme", package=package)

    assert revision.revision == 1
    assert revision.status == "active"
    assert revision.author == "seed"
    assert revision.approver == "seed"
    rule_ids = {rule.id for rule in revision.package.rules}
    assert rule_ids == {"MAKE-001", "SPEC-FMT-001", "CI-001", "API-ERR-001"}

    repo = PolicyRevisionRepository(session)
    active = await repo.active_revision("acme")
    assert active is not None
    assert active.revision == 1
