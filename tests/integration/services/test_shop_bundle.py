"""S72: ショップ題材を DB に用意し（examples/shop/seed.py）、worker で main と各変異の PR を検査。

承認した bundle（仕様・skill・指示）と LLM 支援の規約を、本番と同じ ReviewRunService が使う。
LLM は呼ばない（決定的な偽物）。実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid
import zlib
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from examples.shop.seed import Names, seed
from speclino.db.repositories import FindingRepository, ReviewRunRepository
from speclino.domain.finding import Finding
from speclino.services import knowledge_registry as kr
from tests.unit.examples.test_shop import EXPECTED_CHANGES, MAIN_EXPECTED, changes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

LLM_RULE = "SHOP-ERR-001"
# PR ごとの、確定が効いていない LLM 支援の Finding の区分（変わった単位だけ）
PENDING_LLM: dict[str, list[str]] = {
    # 規約の単位は OrderError に渡したメッセージ（string_literal）。メッセージを変えない PR は
    # main で確定した単位のまま。#2・#7 は新しいメッセージ（指摘なし・指摘あり）で確定待ち
    "01-missing-charge": [],
    "02-ship-from-pending": ["no_issue"],
    "03-refund-on-cancel": [],
    "04-notify-between": [],
    "05-dynamic-charge": [],
    "06-rebind-charge": [],
    "07-terse-error": ["issue"],
}


async def _findings(f: async_sessionmaker, run_id: str) -> tuple[str | None, list[Finding]]:  # type: ignore[type-arg]
    gate, found, _ = await _run(f, run_id)
    return gate, found


async def _run(
    f: async_sessionmaker,  # type: ignore[type-arg]
    run_id: str,
) -> tuple[str | None, list[Finding], set[str]]:
    """(ゲート, Findings, ゲートの cleared の ID)。"""
    async with f() as s:
        run = await ReviewRunRepository(s).get(uuid.UUID(run_id))
        assert run is not None and run.exec_state == "completed", run
        rows = await FindingRepository(s).list_for_run(uuid.UUID(run_id))
    gate = (run.manifest or {}).get("gate") or {}
    return (
        run.gate_status,
        [Finding.model_validate(r.payload) for r in rows],
        set(gate.get("cleared_ids") or []),
    )


async def test_seeded_runs_have_the_intended_verdicts(engine: AsyncEngine, tmp_path: Path) -> None:
    slug = f"e2e-shop-{uuid.uuid4().hex[:8]}"
    names = Names(
        workspace=slug,
        repo=f"e2e/{slug}",
        github_repo_id=zlib.crc32(slug.encode()),
        llm_model=f"fake-{slug}",
    )
    f = async_sessionmaker(engine, expire_on_commit=False)
    result = await seed(f, repo_dir=tmp_path / "repo", artifact_dir=tmp_path / "blobs", names=names)

    # 4 つの承認（仕様・skill・指示・規約）ごとに bundle の世代が進む。作者と承認者は別の人
    assert result.bundle_generation == 4
    async with f() as s:
        revs = [await kr.revision_at(s, aid, 1) for aid in result.artifacts.values()]
    assert {(r.status, r.author, r.approver) for r in revs} == {("active", "spec-author", "judge")}

    gate, main, main_cleared = await _run(f, result.runs["main"])
    lock = [x for x in main if x.requirement_id == "KNOWLEDGE-LOCK-001"]
    assert [x.verdict for x in lock] == ["VERIFIED_IN_SCOPE"]  # sync した lock が main にある
    llm = [x for x in main if x.requirement_id.startswith(LLM_RULE)]
    assert llm and {x.assessment_outcome for x in llm} == {"no_issue"}
    # 必須の規約の「指摘なし」も人の確定まで止める（#114）。seed は main の分を owner が確定済み
    assert {x.id for x in llm} <= main_cleared
    base = {
        (x.requirement_id, x.behavior_id): x.verdict
        for x in main
        if x.requirement_id != "KNOWLEDGE-LOCK-001" and not x.requirement_id.startswith(LLM_RULE)
    }
    assert base == MAIN_EXPECTED
    assert gate == "pass"

    for mutation, expected in EXPECTED_CHANGES.items():
        gate, found, cleared = await _run(f, result.runs[f"pr/{mutation}"])
        got = {
            (x.requirement_id, x.behavior_id): x.verdict
            for x in found
            if not x.requirement_id.startswith(LLM_RULE)
            and x.requirement_id != "KNOWLEDGE-LOCK-001"
        }
        assert changes(MAIN_EXPECTED, got) == expected, mutation
        outcomes = sorted(
            x.assessment_outcome or "" for x in found if x.requirement_id.startswith(LLM_RULE)
        )
        # main で確定した単位は内容が同じなので確定が効く。PR で変わった単位は確定し直すまで止める
        pending = sorted(
            x.assessment_outcome or ""
            for x in found
            if x.requirement_id.startswith(LLM_RULE) and x.id not in cleared
        )
        assert pending == PENDING_LLM[mutation], (mutation, pending)
        if mutation == "07-terse-error":
            # 短すぎるメッセージだけを LLM 支援の規約が REVIEW（issue）にする。確定は人
            assert outcomes == ["issue", "no_issue", "no_issue", "no_issue"], outcomes
        else:
            assert set(outcomes) == {"no_issue"}, (mutation, outcomes)
        assert gate == ("pass" if not expected and not pending else "fail"), mutation


async def test_run_api_returns_the_cleared_ids(engine: AsyncEngine, tmp_path: Path) -> None:
    """run の API はゲートを止めない REVIEW の ID（gate の cleared）を返す。

    画面は判定ごとの件数と失敗の理由をこの集合で数える。
    """

    import httpx

    from speclino.adapters.localgit.local_code_host import LocalCodeHost
    from speclino.adapters.queue.inprocess import InProcessQueue
    from speclino.adapters.storage.fs import FileSystemBlobStore
    from speclino.api.app import create_app
    from speclino.api.deps import AppState
    from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
    from speclino.settings import Settings

    slug = f"e2e-shopapi-{uuid.uuid4().hex[:8]}"
    names = Names(
        workspace=slug,
        repo=f"e2e/{slug}",
        github_repo_id=zlib.crc32(slug.encode()),
        llm_model=f"fake-{slug}",
    )
    f = async_sessionmaker(engine, expire_on_commit=False)
    result = await seed(f, repo_dir=tmp_path / "repo", artifact_dir=tmp_path / "blobs", names=names)
    host = LocalCodeHost(repos={names.repo: tmp_path / "repo"})
    queue = InProcessQueue(lambda _rid: None, immediate=False)  # type: ignore[arg-type, return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(f, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    settings = Settings(env="local", role="all")
    app = create_app(settings, AppState(settings, engine, f, host, queue, blobs, service))
    judge = {"X-Dev-User": "judge"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        # cleared は人が確定した REVIEW だけ（seed が main で確定した「指摘なし」）。PR で変わった
        # メッセージは確定し直すまで cleared に入らない（#114）
        for branch, expected in (
            ("main", 4),
            ("pr/07-terse-error", 3),
            ("pr/01-missing-charge", 4),
        ):
            body = (await c.get(f"/api/review-runs/{result.runs[branch]}", headers=judge)).json()
            by_id = {x["id"]: x for x in body["findings"]}
            cleared = body["cleared_ids"]
            assert len(cleared) == expected, branch
            assert all(by_id[i]["assessment_outcome"] == "no_issue" for i in cleared), branch
            assert body["manifest"]["gate"]["cleared_ids"] == cleared
