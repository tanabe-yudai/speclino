"""人の確定の経路（S70）: REST と PR コメント。実 PostgreSQL が必要。"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services.attestations import attest_as_member, list_attestations
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import register_repo, repo_id_of, set_project_approvers
from tests.integration.services.test_api_flow import (
    SECRET,
    comment_payload,
    pr_payload,
    signed,
)
from tests.integration.services.test_review_run_service import make_demo_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

HASH = "sha256:" + "a" * 64
OWNER = {"X-Dev-User": "owner1"}
APPROVER = {"X-Dev-User": "approver1"}
MEMBER = {"X-Dev-User": "member1"}
STRANGER = {"X-Dev-User": "owner9"}


class Ctx:
    def __init__(
        self,
        c: httpx.AsyncClient,
        slug: str,
        other: str,
        repo: str,
        other_repo: str,
        shas: tuple[str, str],
    ) -> None:
        self.c, self.slug, self.other, self.repo, self.other_repo = c, slug, other, repo, other_repo
        self.base, self.head = shas


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-at-{uuid.uuid4().hex[:8]}"
    other = f"e2e-ato-{uuid.uuid4().hex[:8]}"
    repo, other_repo = f"e2e/{slug}", f"e2e/{other}"
    await register_repo(
        engine,
        repo,
        {"owner1": "owner", "approver1": "approver", "member1": "member", "dev": "approver"},
        team=slug,
    )
    await register_repo(engine, other_repo, {"owner9": "owner"}, team=other)
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(
        repos={repo: tmp_path / "repo", other_repo: tmp_path / "repo"},
        repo_ids={repo: repo_id_of(repo), other_repo: repo_id_of(other_repo)},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", github_webhook_secret=SECRET)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield Ctx(c, slug, other, repo, other_repo, (base, head))


async def _pr_run(ctx: Ctx, repo: str, number: int = 1) -> str:
    raw, headers = signed(pr_payload(repo, ctx.head, ctx.base, number), "pull_request")
    res = await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    return str(res.json()["run_id"])


async def _stored(engine: AsyncEngine, slug: str) -> list[tuple[str, str, str]]:
    async with async_sessionmaker(engine)() as s:
        return [(a.fingerprint, a.disposition, a.actor) for a in await list_attestations(s, slug)]


async def _audit(engine: AsyncEngine, slug: str) -> list[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            sql_text(
                "SELECT actor, decision FROM audit_events WHERE action = 'attestation.record' "
                "AND target LIKE :t ORDER BY ts"
            ),
            {"t": f"{slug}:%"},
        )
        return [(r[0], r[1]) for r in rows]


async def test_rest_attest_is_for_owner_and_approver_only(ctx: Ctx, engine: AsyncEngine) -> None:
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {"fingerprint": "R:i:1", "content_hash": HASH, "disposition": "attested"}
    assert (await ctx.c.post(url, json=body, headers=MEMBER)).status_code == 403
    assert (await ctx.c.post(url, json=body, headers=STRANGER)).status_code == 404
    bad = {**body, "content_hash": "sha256:xyz"}
    assert (await ctx.c.post(url, json=bad, headers=OWNER)).status_code == 422
    ok = await ctx.c.post(url, json=body, headers=OWNER)
    assert ok.status_code == 200, ok.text
    assert ok.json()["attestation"]["actor"] == "owner1"
    assert ok.json()["recheck_run_id"] is None
    ack = {**body, "disposition": "acknowledged_violation"}
    assert (await ctx.c.post(url, json=ack, headers=APPROVER)).status_code == 200
    assert await _stored(engine, ctx.slug) == [("R:i:1", "acknowledged_violation", "approver1")]
    assert await _stored(engine, ctx.other) == []
    assert await _audit(engine, ctx.slug) == [
        ("member1", "rejected"),
        ("owner1", "attested"),
        ("approver1", "acknowledged_violation"),
    ]


async def test_rest_attest_rechecks_the_pr_head(ctx: Ctx) -> None:
    run_id = await _pr_run(ctx, ctx.repo)
    other_run = await _pr_run(ctx, ctx.other_repo)
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {"fingerprint": "R:i:1", "content_hash": HASH, "disposition": "attested"}
    res = await ctx.c.post(url, json={**body, "run_id": run_id}, headers=OWNER)
    assert res.status_code == 200, res.text
    recheck = res.json()["recheck_run_id"]
    detail = (await ctx.c.get(f"/api/review-runs/{recheck}", headers=OWNER)).json()
    assert detail["trigger"] == "attestation" and detail["head_sha"] == ctx.head
    assert detail["exec_state"] == "completed"
    assert detail["workspace"] == ctx.slug  # 画面が確定の宛先を決める
    # 別のワークスペースの run は 404（確定も巻き戻る）
    res = await ctx.c.post(url, json={**body, "run_id": other_run}, headers=OWNER)
    assert res.status_code == 404


async def test_pr_comment_attest_is_recorded_for_approvers(ctx: Ctx, engine: AsyncEngine) -> None:
    await _pr_run(ctx, ctx.repo)
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    raw, headers = signed(
        comment_payload(ctx.repo, 1, f"/speclino attest R:i:2 {HASH}", "stranger"),
        "issue_comment",
    )
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["status"] == "handled" and res["commands"][0]["accepted"] is False
    assert await _stored(engine, ctx.slug) == []

    raw, headers = signed(
        comment_payload(ctx.repo, 1, f"/speclino attest R:i:2 {HASH}", "gh-approver"),
        "issue_comment",
    )
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["status"] == "queued", res  # 確定の後は再検査の run
    assert await _stored(engine, ctx.slug) == [("R:i:2", "attested", "github:gh-approver")]

    raw, headers = signed(
        comment_payload(ctx.repo, 1, f"/speclino attest R:i:2 {HASH} ack", "gh-approver"),
        "issue_comment",
    )
    await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    assert await _stored(engine, ctx.slug) == [
        ("R:i:2", "acknowledged_violation", "github:gh-approver")
    ]

    raw, headers = signed(
        comment_payload(ctx.repo, 1, "/speclino attest R:i:2 not-a-hash", "gh-approver"),
        "issue_comment",
    )
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is False


async def _audit_detail(engine: AsyncEngine, slug: str) -> list[tuple[str, object, object]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            sql_text(
                "SELECT actor, detail FROM audit_events WHERE action = 'attestation.record' "
                "AND decision <> 'rejected' AND target LIKE :t ORDER BY ts"
            ),
            {"t": f"{slug}:%"},
        )
        return [(r[0], r[1].get("self_attested"), r[1].get("warning")) for r in rows]


DEV_ID, OTHER_ID = 1001, 2002


async def _pr_run_by(ctx: Ctx, author_id: int | None) -> str:
    payload = pr_payload(ctx.repo, ctx.head, ctx.base, 1)
    if author_id is not None:
        payload["pull_request"]["user"]["id"] = author_id
    raw, headers = signed(payload, "pull_request")
    res = await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    return str(res.json()["run_id"])


async def _attest_via_service(
    engine: AsyncEngine, ctx: Ctx, run_id: str, uid: str, fp: str, linked: dict[str, int]
) -> object:
    async def github_id_of(u: str) -> int | None:
        return linked.get(u)

    async with async_sessionmaker(engine, expire_on_commit=False)() as s, s.begin():
        saved, _ = await attest_as_member(
            s,
            workspace=ctx.slug,
            uid=uid,
            fingerprint=fp,
            content_hash=HASH,
            disposition="attested",
            run_id=uuid.UUID(run_id),
            github_id_of=github_id_of,
        )
    return saved.self_attested


async def test_self_attestation_is_matched_by_github_id(ctx: Ctx, engine: AsyncEngine) -> None:
    """確定者の連携済み GitHub ID と PR の作成者の ID で照合する（ログイン名・uid は使わない）。"""
    run_id = await _pr_run_by(ctx, DEV_ID)
    linked = {"approver1": DEV_ID, "owner1": OTHER_ID}
    assert await _attest_via_service(engine, ctx, run_id, "approver1", "R:i:1", linked) is True
    assert await _attest_via_service(engine, ctx, run_id, "owner1", "R:i:2", linked) is False
    # 未連携は照合不可（null）。黙って false にしない
    assert await _attest_via_service(engine, ctx, run_id, "dev", "R:i:3", linked) is None
    cfg = f"KNOWLEDGE-BINDINGS-001:{HASH}"
    assert await _attest_via_service(engine, ctx, run_id, "approver1", cfg, linked) is True
    audit = await _audit_detail(engine, ctx.slug)
    assert [(a, m) for a, m, _ in audit] == [
        ("approver1", True),
        ("owner1", False),
        ("dev", None),
        ("approver1", True),
    ]
    # 対応・規約の変更の自己確定は監査に警告（拒否はしない）
    assert [w is not None for _, _, w in audit] == [False, False, False, True]


async def test_self_attestation_is_null_without_author_id(ctx: Ctx, engine: AsyncEngine) -> None:
    run_id = await _pr_run_by(ctx, None)
    linked = {"approver1": DEV_ID}
    assert await _attest_via_service(engine, ctx, run_id, "approver1", "R:i:4", linked) is None
    # REST（連携の無い現状）は照合不可で記録する
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {
        "fingerprint": "R:i:5",
        "content_hash": HASH,
        "disposition": "attested",
        "run_id": run_id,
    }
    res = await ctx.c.post(url, json=body, headers=APPROVER)
    assert res.status_code == 200 and res.json()["attestation"]["self_attested"] is None


async def test_pr_comment_self_attestation_is_matched_by_id(ctx: Ctx, engine: AsyncEngine) -> None:
    await _pr_run_by(ctx, DEV_ID)
    await set_project_approvers(engine, ctx.repo, ["dev", "gh-approver"])
    cases = (("dev", DEV_ID, "R:i:6", True), ("gh-approver", OTHER_ID, "R:i:7", False))
    for login, user_id, fp, expected in cases:
        payload = comment_payload(ctx.repo, 1, f"/speclino attest {fp} {HASH}", login)
        payload["comment"]["user"]["id"] = user_id
        payload["issue"]["user"] = {"login": "dev", "id": DEV_ID}  # PR の作成者
        raw, headers = signed(payload, "issue_comment")
        res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
        assert res["commands"][0]["self_attested"] is expected, res
    # ID の無いペイロードは照合不可
    payload = comment_payload(ctx.repo, 1, f"/speclino attest R:i:8 {HASH}", "dev")
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["self_attested"] is None
    async with async_sessionmaker(engine)() as s:
        marks = {a.fingerprint: a.self_attested for a in await list_attestations(s, ctx.slug)}
    assert marks == {"R:i:6": True, "R:i:7": False, "R:i:8": None}
