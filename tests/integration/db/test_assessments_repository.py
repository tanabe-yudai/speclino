"""`llm_assessments` / `attestations` の repository（S70）。実 PostgreSQL が必要。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from speclino.db.models import Base
from speclino.db.repositories.assessments import AssessmentRepository, AttestationRepository
from speclino.domain.assessment import AssessmentKey, AssessmentRecord, ItemVerdict

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
_HASH = "sha256:" + "a" * 64
_OTHER = "sha256:" + "b" * 64

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DATABASE_URL is not None
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = async_sessionmaker(bind=conn, expire_on_commit=False)()
        await s.execute(
            text("INSERT INTO teams (team, approvers) VALUES ('ws-a', '[]'), ('ws-b', '[]')")
        )
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()
    await engine.dispose()


def _record(outcome: str = "issue") -> AssessmentRecord:
    return AssessmentRecord(
        key=AssessmentKey(
            policy_revision=2,
            rule_id="ERRMSG-NEXT",
            unit_hash=_HASH,
            model="m",
            prompt_version="v1+abc",
        ),
        unit_symbol="app/api.py::f",
        items=[
            ItemVerdict(
                item_id="next",
                outcome=outcome,  # type: ignore[arg-type]
                votes=[True, True, False],
                quote="q",
            )
        ],
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
    )


async def test_assessment_round_trip_and_first_record_wins(session: AsyncSession) -> None:
    repo = AssessmentRepository(session, "ws-a")
    assert await repo.get(_record().key) is None
    await repo.put(_record())
    await repo.put(_record("no_issue"))  # 同じキーは上書きしない
    got = await repo.get(_record().key)
    assert got == _record()


async def test_attestation_upsert_and_workspace_isolation(session: AsyncSession) -> None:
    repo = AttestationRepository(session)
    first, first_actor = await repo.upsert(
        workspace="ws-a",
        fingerprint="R:i:1",
        content_hash=_HASH,
        disposition="attested",
        actor="u1",
    )
    assert first.disposition == "attested" and first_actor == "u1"
    changed, still_first = await repo.upsert(
        workspace="ws-a",
        fingerprint="R:i:1",
        content_hash=_HASH,
        disposition="acknowledged_violation",
        actor="u2",
    )
    assert (changed.disposition, changed.actor) == ("acknowledged_violation", "u2")
    assert still_first == "u1"  # 最初の確定者は変えない
    await repo.upsert(
        workspace="ws-a",
        fingerprint="R:i:1",
        content_hash=_OTHER,
        disposition="attested",
        actor="u1",
    )
    await repo.upsert(
        workspace="ws-b", fingerprint="R:i:1", content_hash=_HASH, disposition="attested", actor="x"
    )
    rows = await repo.list_for_workspace("ws-a")
    assert [(r.content_hash, r.disposition) for r in rows] == [
        (_HASH, "acknowledged_violation"),
        (_OTHER, "attested"),
    ]
    assert {r.workspace for r in rows} == {"ws-a"}


async def test_assessments_are_scoped_by_workspace(session: AsyncSession) -> None:
    """同じキーでも別のワークスペースの記録は見えない（別々に評価する）。"""
    a = AssessmentRepository(session, "ws-a")
    b = AssessmentRepository(session, "ws-b")
    await a.put(_record())
    assert await b.get(_record().key) is None
    await b.put(_record("no_issue"))
    got_a, got_b = await a.get(_record().key), await b.get(_record().key)
    assert got_a is not None and got_a.items[0].outcome == "issue"
    assert got_b is not None and got_b.items[0].outcome == "no_issue"


async def test_attestation_keeps_the_self_attested_mark(session: AsyncSession) -> None:
    repo = AttestationRepository(session)
    saved, _ = await repo.upsert(
        workspace="ws-a",
        fingerprint="R:i:9",
        content_hash=_HASH,
        disposition="attested",
        actor="u1",
        self_attested=True,
    )
    assert saved.self_attested
    assert [a.self_attested for a in await repo.list_for_workspace("ws-a")] == [True]


async def test_reattestation_keeps_the_strongest_self_mark(session: AsyncSession) -> None:
    """確定し直しても自己確定の印は true > null > false で強い方を保つ。"""
    repo = AttestationRepository(session)

    async def put(fp: str, mark: bool | None, actor: str = "u") -> bool | None:
        saved, _ = await repo.upsert(
            workspace="ws-a",
            fingerprint=fp,
            content_hash=_HASH,
            disposition="attested",
            actor=actor,
            self_attested=mark,
        )
        return saved.self_attested

    assert await put("A", True) is True
    assert await put("A", False, "other") is True  # 自己確定の印は消えない
    assert await put("A", None) is True
    assert await put("B", None) is None
    assert await put("B", False) is None  # 照合不可は「作成者以外」に上書きされない
    assert await put("B", True) is True
    assert await put("C", False) is False
    assert await put("C", None) is None
