"""`knowledge_chats` の repository（S73）。実 PostgreSQL が必要。

すべての問い合わせは (workspace, owner_uid) で絞る。他人（同じワークスペースの owner を含む）と
別のワークスペースの会話は、読むことも変えることも消すこともできない。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from speclino.db.models import Base
from speclino.db.repositories.knowledge_chats import KnowledgeChatRepository
from speclino.domain.knowledge_chat import ChatMessage

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
A1 = uuid.UUID(int=0xC1)

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
            text("INSERT INTO teams (team, approvers) VALUES ('kc-a', '[]'), ('kc-b', '[]')")
        )
        await s.execute(
            text(
                "INSERT INTO artifacts (id, workspace, kind, name) "
                "VALUES (:id, 'kc-a', 'spec', 'x')"
            ),
            {"id": A1},
        )
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()
    await engine.dispose()


def _msg(text_: str) -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4().hex, role="user", text=text_, status="done", created_at=datetime.now(UTC)
    )


async def test_owner_reads_lists_and_others_cannot(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    mine = await repo.create(
        workspace="kc-a", owner_uid="alice", kind="spec", artifact_id=A1, title="注文"
    )
    await repo.create(
        workspace="kc-a", owner_uid="alice", kind="policy", artifact_id=None, title="p"
    )
    await repo.create(workspace="kc-a", owner_uid="bob", kind="spec", artifact_id=None, title="b")
    await repo.create(workspace="kc-b", owner_uid="alice", kind="spec", artifact_id=None, title="o")
    assert mine.version == 1 and mine.messages == [] and mine.artifact_id == str(A1)

    cid = uuid.UUID(mine.id)
    got = await repo.get("kc-a", "alice", cid)
    assert got is not None and got.title == "注文"
    # 同じワークスペースの他人（owner であっても）・別のワークスペースからは見えない
    assert await repo.get("kc-a", "bob", cid) is None
    assert await repo.get("kc-b", "alice", cid) is None

    rows = await repo.list_for_owner("kc-a", "alice")
    assert [r.title for r in rows] == ["p", "注文"]  # 新しい順
    assert [r.title for r in await repo.list_for_owner("kc-a", "alice", kind="spec")] == ["注文"]
    assert [r.title for r in await repo.list_for_owner("kc-a", "alice", artifact_id=A1)] == ["注文"]
    assert [r.title for r in await repo.list_for_owner("kc-a", "bob")] == ["b"]


async def test_update_is_version_checked_and_owner_scoped(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    chat = await repo.create(
        workspace="kc-a", owner_uid="alice", kind="spec", artifact_id=None, title="t"
    )
    cid = uuid.UUID(chat.id)
    assert await repo.update("kc-a", "bob", cid, expected_version=1, title="x") is None
    assert await repo.update("kc-a", "alice", cid, expected_version=2, title="x") is None
    updated = await repo.update("kc-a", "alice", cid, expected_version=1, title="x", artifact_id=A1)
    assert updated is not None and updated.version == 2 and updated.artifact_id == str(A1)


async def test_save_messages_bumps_version(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    chat = await repo.create(
        workspace="kc-a", owner_uid="alice", kind="skill", artifact_id=None, title="t"
    )
    cid = uuid.UUID(chat.id)
    locked = await repo.get("kc-a", "alice", cid, for_update=True)
    assert locked is not None
    saved = await repo.save_messages("kc-a", "alice", cid, [_msg("こんにちは")])
    assert saved is not None and saved.version == 2 and saved.messages[0].text == "こんにちは"
    assert saved.updated_at >= chat.updated_at
    assert await repo.save_messages("kc-a", "bob", cid, []) is None
    (row,) = await repo.list_for_owner("kc-a", "alice")
    assert row.message_count == 1


async def test_delete_and_purge(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    a = await repo.create(
        workspace="kc-a", owner_uid="alice", kind="spec", artifact_id=None, title=""
    )
    await repo.create(workspace="kc-a", owner_uid="alice", kind="spec", artifact_id=None, title="")
    await repo.create(workspace="kc-a", owner_uid="bob", kind="spec", artifact_id=None, title="")
    assert not await repo.delete("kc-a", "bob", uuid.UUID(a.id))
    assert await repo.delete("kc-a", "alice", uuid.UUID(a.id))
    assert await repo.get("kc-a", "alice", uuid.UUID(a.id)) is None
    assert await repo.delete_for_owner("kc-a", "alice") == 1
    assert await repo.list_for_owner("kc-a", "alice") == []
    assert len(await repo.list_for_owner("kc-a", "bob")) == 1


async def test_policy_chat_cannot_bind_and_artifact_delete_unbinds(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    chat = await repo.create(
        workspace="kc-a", owner_uid="alice", kind="spec", artifact_id=A1, title=""
    )
    await session.execute(text("DELETE FROM artifacts WHERE id = :id"), {"id": A1})
    got = await repo.get("kc-a", "alice", uuid.UUID(chat.id))
    assert got is not None and got.artifact_id is None
    with pytest.raises(Exception, match="ck_knowledge_chats_policy_unbound"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO artifacts (id, workspace, kind, name) "
                    "VALUES (:id, 'kc-a', 'spec', 'y')"
                ),
                {"id": A1},
            )
            await repo.create(
                workspace="kc-a", owner_uid="alice", kind="policy", artifact_id=A1, title=""
            )


async def test_workspace_delete_cascades(session: AsyncSession) -> None:
    repo = KnowledgeChatRepository(session)
    await repo.create(workspace="kc-b", owner_uid="alice", kind="spec", artifact_id=None, title="")
    await session.execute(text("DELETE FROM teams WHERE team = 'kc-b'"))
    n = (
        await session.execute(text("SELECT count(*) FROM knowledge_chats WHERE workspace = 'kc-b'"))
    ).scalar_one()
    assert n == 0
