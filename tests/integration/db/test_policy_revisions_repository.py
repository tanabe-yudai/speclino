"""`PolicyRevisionRepository` / `services.policy_governance` の統合試験（S42）。

実 PostgreSQL が必要。`SPECLINO_DATABASE_URL` が未設定ならモジュール全体を skip する
（`tests/integration/db/test_repositories.py` と同じ理由・同じフィクスチャ構成）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from speclino.db.models import AuditEvent, Base
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.teams import TeamRepository
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
    # 承認者の検証（統治の穴修正 #1）が入ったので、承認前に "bob" をチームの承認者として
    # 登録する必要がある（登録しなければ approve が PermissionError で拒否する）。
    await TeamRepository(session).set_approvers("acme", ["bob"])
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
    # SPEC-TEST-001 は本タスクと無関係な別ストーリー（S49、#59）で speclino-core.yaml に
    # 追加された規約。ここでの変更ではないが、既存アサーションが古いままだと本タスクの
    # 統合テストが赤くなるため、実際の内容に合わせる。
    assert rule_ids == {"MAKE-001", "SPEC-FMT-001", "SPEC-TEST-001", "CI-001", "API-ERR-001"}

    repo = PolicyRevisionRepository(session)
    active = await repo.active_revision("acme")
    assert active is not None
    assert active.revision == 1


# --------------------------------------------------------------- 統治の穴修正の回帰試験


async def test_approve_denies_non_approver_and_records_denied_audit(
    session: AsyncSession,
) -> None:
    """穴 #1: 承認者の許可集合に無い ID からの承認は PermissionError で拒否し、
    `audit_events` に `decision="denied"` を残す。"""
    await TeamRepository(session).set_approvers("acme", ["bob"])
    revision = await policy_governance.create_draft(
        session, team="acme", package=_package(), author="alice"
    )

    with pytest.raises(PermissionError, match="承認者ではない"):
        await policy_governance.approve(
            session, team="acme", revision=revision.revision, approver="mallory"
        )

    # 承認は成立していない（draft のまま）。
    repo = PolicyRevisionRepository(session)
    still_draft = await repo.revision_at("acme", revision.revision)
    assert still_draft is not None
    assert still_draft.status == "draft"

    rows = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.target == f"acme@{revision.revision}")
            )
        )
        .scalars()
        .all()
    )
    denied = [r for r in rows if r.action == "policy.approve" and r.decision == "denied"]
    assert len(denied) == 1
    assert denied[0].actor == "mallory"


async def test_approve_denies_everyone_when_team_has_no_approvers_registered(
    session: AsyncSession,
) -> None:
    """穴 #1: 承認者が1人も登録されていないチーム（`teams` に行が無い場合を含む）では
    誰も承認できない（安全側のデフォルト）。"""
    revision = await policy_governance.create_draft(
        session, team="nobody-registered", package=_package(), author="alice"
    )

    with pytest.raises(PermissionError):
        await policy_governance.approve(
            session, team="nobody-registered", revision=revision.revision, approver="alice"
        )


async def test_seed_from_package_rejects_when_team_already_has_a_revision(
    session: AsyncSession,
) -> None:
    """穴 #2: 既に改訂があるチームへの seed は拒否し、既存の active をそのまま残す。"""
    await TeamRepository(session).set_approvers("acme", ["bob"])
    existing = await policy_governance.create_draft(
        session, team="acme", package=_package("R-EXISTING"), author="alice"
    )
    await policy_governance.approve(
        session, team="acme", revision=existing.revision, approver="bob"
    )

    source = REPO_ROOT / ".speclino" / "policy" / "speclino-core.yaml"
    package = PolicyPackage.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))

    with pytest.raises(ValueError, match="seed は初期化専用"):
        await policy_governance.seed_from_package(session, team="acme", package=package)

    repo = PolicyRevisionRepository(session)
    active = await repo.active_revision("acme")
    assert active is not None
    assert active.revision == existing.revision
    assert active.package.rules[0].id == "R-EXISTING"  # 既存の active が差し替わっていない
    assert len(await repo.list_revisions("acme")) == 1  # seed が新しい改訂を作っていない


async def test_concurrent_approve_second_transaction_fails_and_does_not_overwrite_approver() -> (
    None
):
    """穴 #3: 2つのトランザクションが同じ draft を同時に承認しようとした場合、後から
    commit しようとした側の UPDATE は `WHERE ... AND status='draft'` の条件に阻まれて
    0 行更新となり失敗する。先に commit した側の approver は上書きされない。

    `session` フィクスチャは1テストにつき1トランザクション（テスト終了時に rollback）
    という構成なので、ここでは真の並行性（2つの独立したコミット）を再現するために
    専用の engine と2つの独立した session を使う。テスト後に自分でデータを削除する。
    """
    assert _DATABASE_URL is not None
    team = f"race-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(_DATABASE_URL)
    session_a: AsyncSession | None = None
    session_b: AsyncSession | None = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async with session_factory() as setup, setup.begin():
            draft = await PolicyRevisionRepository(setup).create_draft(
                team=team, package=_package(), author="alice"
            )

        session_a = session_factory()
        session_b = session_factory()
        b_started = asyncio.Event()
        a_may_commit = asyncio.Event()
        outcome: dict[str, str] = {}

        async def run_a() -> None:
            assert session_a is not None
            async with session_a.begin():
                await PolicyRevisionRepository(session_a).approve(
                    team=team, revision=draft.revision, approver="bob"
                )
                b_started.set()
                # A はまだコミットしない: B が同じ行を承認しようとして競合するのを待つ。
                await a_may_commit.wait()
            # ここで `async with` を正常終了して commit する。

        async def run_b() -> None:
            assert session_b is not None
            await b_started.wait()
            try:
                async with session_b.begin():
                    await PolicyRevisionRepository(session_b).approve(
                        team=team, revision=draft.revision, approver="carol"
                    )
                outcome["result"] = "unexpected_success"
            except ValueError as e:
                outcome["result"] = str(e)

        async def coordinator() -> None:
            await b_started.wait()
            # B の UPDATE が A の未コミットの行ロックにぶつかってサーバ側で
            # 待たされる状態に確実に入るまで少し待ってから、A のコミットを許可する。
            await asyncio.sleep(0.3)
            a_may_commit.set()

        await asyncio.wait_for(asyncio.gather(run_a(), run_b(), coordinator()), timeout=10)

        assert "draft でない" in outcome["result"]

        async with session_factory() as check:
            final = await PolicyRevisionRepository(check).revision_at(team, draft.revision)
        assert final is not None
        assert final.status == "active"
        assert final.approver == "bob"  # carol に上書きされていない
    finally:
        if session_a is not None:
            await session_a.close()
        if session_b is not None:
            await session_b.close()
        async with engine.begin() as conn:
            await conn.execute(
                Base.metadata.tables["policy_revisions"]
                .delete()
                .where(Base.metadata.tables["policy_revisions"].c.team == team)
            )
        await engine.dispose()
