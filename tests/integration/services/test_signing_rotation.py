"""署名鍵の更新（#114）: 前の鍵の bundle の検証と `rebundle_all`。実 PostgreSQL が必要。"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr
from speclino.services.signing_rotation import rebundle_all
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

OLD, NEW = "old-secret-for-rotation", "new-secret-for-rotation"
OLD_KID = bundle_signing.key_id_of(OLD.encode())
NEW_KID = bundle_signing.key_id_of(NEW.encode())


@pytest.fixture(autouse=True)
def _reset_key() -> Iterator[None]:
    yield
    bundle_signing.configure(None)


async def test_rotation_verifies_old_bundles_and_rebundles(engine: AsyncEngine) -> None:
    slug = f"e2e-rot-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = str(await project_id_of(engine, repo))
    f = async_sessionmaker(engine, expire_on_commit=False)

    bundle_signing.configure(Settings(bundle_signing_key=OLD))
    async with f() as s, s.begin():
        old = await kr.ensure_bundle(s, slug)
    assert old.key_id == OLD_KID

    # 鍵を更新し、前の鍵を検証用に残す → 前の bundle もそのまま使える
    bundle_signing.configure(
        Settings(bundle_signing_key=NEW, bundle_signing_keys_old=f"{OLD_KID}:{OLD}")
    )
    assert kr.verify_bundle(old)
    async with f() as s:
        await kr.resolve_for_project(s, old, pid)

    async with f() as s, s.begin():
        (outcome,) = await rebundle_all(s, only=[slug])
    assert (outcome.status, outcome.previous_generation, outcome.previous_key_id) == (
        "rebundled",
        old.generation,
        OLD_KID,
    )
    async with f() as s:
        new = await kr.current_bundle(s, slug)
    assert new is not None and new.generation == old.generation + 1
    assert new.key_id == NEW_KID and kr.verify_bundle(new)

    # 今の鍵で署名済みなら飛ばす（--force で作り直す）
    async with f() as s, s.begin():
        (again,) = await rebundle_all(s, only=[slug])
        assert again.status == "skipped"
        (forced,) = await rebundle_all(s, force=True, only=[slug])
        assert forced.status == "rebundled" and forced.generation == new.generation + 1

    # 前の鍵を外すと、前の鍵の bundle は検証できない（run は失敗する。フェイルクローズ）
    bundle_signing.configure(Settings(bundle_signing_key=NEW))
    assert not kr.verify_bundle(old)
    async with f() as s:
        with pytest.raises(ValueError, match="署名"):
            await kr.resolve_for_project(s, old, pid)
    assert kr.verify_bundle(new)
