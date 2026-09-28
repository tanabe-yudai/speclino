"""repositories の統合試験（S14）。実 PostgreSQL が必要。

`SPECLINO_DATABASE_URL` が未設定ならモジュール全体を skip する（`Settings` の既定値には
フォールバックしない。フォールバックすると DB 未起動時に skip ではなく接続エラーになるため）。

各テストは 1 コネクション上でトランザクションを張り、終了時に rollback する
（`Base.metadata.create_all` によるスキーマ作成だけは別コネクションで commit 済み）。
そのためテスト間でデータは残らず、固定値のテストデータを使い回せる。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from speclino.db.models import Base
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.db.repositories.run_events import RunEventRepository
from speclino.db.repositories.webhook_deliveries import WebhookDeliveryRepository

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")

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
    """1 トランザクションにテストを閉じ込め、終了時に必ず rollback する。"""
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


@pytest_asyncio.fixture
async def project_id(session: AsyncSession) -> uuid.UUID:
    repo = ProjectRepository(session)
    project, _ = await repo.resolve_for_webhook(
        github_repo_id=990001, repo_full_name="tanabe-yudai/spec-lino-test", installation_id=None
    )
    return project.id


async def test_try_acquire_returns_false_on_second_call(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    repo = ReviewRunRepository(session)
    run = await repo.create(
        project_id=project_id,
        pr_number=1,
        head_sha="a" * 40,
        base_sha=None,
        trigger="push",
    )

    first = await repo.try_acquire(run.id)
    second = await repo.try_acquire(run.id)

    assert first is True
    assert second is False


async def test_mark_if_new_returns_false_on_second_call(session: AsyncSession) -> None:
    repo = WebhookDeliveryRepository(session)

    first = await repo.mark_if_new("delivery-1", "pull_request", "opened")
    second = await repo.mark_if_new("delivery-1", "pull_request", "opened")

    assert first is True
    assert second is False


async def test_supersede_open_runs_marks_other_open_runs(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    repo = ReviewRunRepository(session)
    run_a = await repo.create(
        project_id=project_id, pr_number=7, head_sha="a" * 40, base_sha=None, trigger="push"
    )
    run_b = await repo.create(
        project_id=project_id, pr_number=7, head_sha="b" * 40, base_sha=None, trigger="push"
    )
    run_c = await repo.create(
        project_id=project_id, pr_number=7, head_sha="c" * 40, base_sha=None, trigger="push"
    )
    # queued 以外の状態も superseded の対象になることを確認する。
    acquired = await repo.try_acquire(run_b.id)
    assert acquired is True

    count = await repo.supersede_open_runs(project_id, 7, except_run_id=run_c.id)
    assert count == 2

    # bulk UPDATE は ORM の識別マップを更新しないため、明示的に refresh して再読込する。
    await session.refresh(run_a)
    await session.refresh(run_b)
    await session.refresh(run_c)
    assert run_a.exec_state == "superseded"
    assert run_b.exec_state == "superseded"
    assert run_c.exec_state == "queued"


async def test_run_events_seq_increments_1_2_3(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    review_run_repo = ReviewRunRepository(session)
    run = await review_run_repo.create(
        project_id=project_id,
        pr_number=9,
        head_sha="d" * 40,
        base_sha=None,
        trigger="push",
    )
    repo = RunEventRepository(session)

    seq1 = await repo.append(run.id, "plan_started", {})
    seq2 = await repo.append(run.id, "tool_call", {"tool": "analyze_focus"})
    seq3 = await repo.append(run.id, "plan_finished", {})

    assert (seq1, seq2, seq3) == (1, 2, 3)

    events = await repo.list_for_run(run.id)
    assert [event.seq for event in events] == [1, 2, 3]


async def test_cost_ledger_total_for_day(session: AsyncSession) -> None:
    repo = CostLedgerRepository(session)
    today = datetime.now(UTC).date()
    other_day = today - timedelta(days=1)

    await repo.add(
        day=today,
        run_id=None,
        estimated_usd=Decimal("0.123456"),
        input_tokens=100,
        output_tokens=50,
        model="gemini-3.8-flash",
    )
    await repo.add(
        day=today,
        run_id=None,
        estimated_usd=Decimal("0.876544"),
        input_tokens=200,
        output_tokens=80,
        model="gemini-3.8-flash",
    )
    await repo.add(
        day=other_day,
        run_id=None,
        estimated_usd=Decimal("5.000000"),
        input_tokens=1,
        output_tokens=1,
        model="gemini-3.8-flash",
    )

    total = await repo.total_for_day(today)
    assert total == Decimal("1.000000")


async def test_cost_ledger_total_for_day_with_no_entries_is_zero(session: AsyncSession) -> None:
    repo = CostLedgerRepository(session)
    empty_day = date(1999, 1, 1)

    total = await repo.total_for_day(empty_day)
    assert total == Decimal(0)


async def test_resolve_for_webhook_follows_renames_by_id(session: AsyncSession) -> None:
    """S53: 同じ ID のリポジトリは改名されても同じ project（team・承認者を保つ）。"""
    projects = ProjectRepository(session)
    first, _ = await projects.resolve_for_webhook(
        github_repo_id=990101, repo_full_name="acme/old-name", installation_id=1
    )
    first.settings = {"approvers": ["alice"]}
    await session.flush()
    renamed, evicted = await projects.resolve_for_webhook(
        github_repo_id=990101, repo_full_name="acme/new-name", installation_id=None
    )
    assert renamed.id == first.id and evicted == []
    assert renamed.repo_full_name == "acme/new-name"
    assert renamed.installation_id == 1  # None では上書きしない
    assert renamed.settings == {"approvers": ["alice"]}


async def test_a_new_repository_reusing_an_old_name_does_not_inherit_the_old_project(
    session: AsyncSession,
) -> None:
    """S53: 名前が同じでも ID が違えば別リポジトリ。旧行は退避され、登録（team）も外れる。"""
    await session.execute(
        text("INSERT INTO teams (team, approvers) VALUES ('ws-a', '[]') ON CONFLICT DO NOTHING")
    )
    projects = ProjectRepository(session)
    old, _ = await projects.resolve_for_webhook(
        github_repo_id=990201, repo_full_name="acme/reused", installation_id=1
    )
    old.team = "ws-a"
    await session.flush()

    new, evicted = await projects.resolve_for_webhook(
        github_repo_id=990202, repo_full_name="acme/reused", installation_id=1
    )
    assert new.id != old.id and new.team is None
    assert evicted == ["acme/reused"]
    await session.refresh(old)
    assert old.repo_full_name.startswith("acme/reused#stale-") and old.team is None


async def test_a_legacy_row_without_an_id_is_not_adopted_by_name(session: AsyncSession) -> None:
    """S53: ID の無い既存行（S53 以前）に、名前が一致するだけで ID を埋めない。"""
    await session.execute(
        text(
            "INSERT INTO projects (id, repo_full_name, default_branch, settings) "
            "VALUES (gen_random_uuid(), 'acme/legacy', 'main', '{}')"
        )
    )
    projects = ProjectRepository(session)
    legacy = await projects.get_by_repo("acme/legacy")
    assert legacy is not None and legacy.github_repo_id is None

    resolved, evicted = await projects.resolve_for_webhook(
        github_repo_id=990301, repo_full_name="acme/legacy", installation_id=1
    )
    assert resolved.id != legacy.id and resolved.github_repo_id == 990301
    assert evicted == ["acme/legacy"]
