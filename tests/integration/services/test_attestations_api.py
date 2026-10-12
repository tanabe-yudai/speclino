"""人の確定の経路（S70）: REST と PR コメント。実 PostgreSQL が必要。"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from speclino.services.attestations import (
    attest,
    attest_as_member,
    attest_if_absent,
    list_attestations,
)
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import (
    login_id,
    project_id_of,
    register_repo,
    repo_id_of,
    set_project_approvers,
)
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
# PR コメントの確定の actor は GitHub のユーザー ID（S61 の承認者の照合と同じく、
# ログイン名は使わない）
GH_APPROVER = f"github:{login_id('gh-approver')}"
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


async def _seed_targets(engine: AsyncEngine, run_id: str, *fingerprints: str) -> None:
    """run の Findings に確定できる指摘（指紋と hash）を足す（PR の検査に出たものとして）。"""
    from speclino.db.models import FindingRecord
    from speclino.domain.finding import Finding

    async with async_sessionmaker(engine)() as s, s.begin():
        for fp in fingerprints:
            outcome = None if fp.startswith("KNOWLEDGE-BINDINGS-001:") else "issue"
            f = Finding(
                id=f"seed:{fp}",
                requirement_id="KNOWLEDGE-BINDINGS-001" if outcome is None else "R:app.py",
                behavior_id="seed",
                verdict="REVIEW",
                category="potential_violation",
                severity="high",
                message="m",
                claim_scope="persisted_state",
                evidence_refs=[f"fingerprint:{fp}", f"content_hash:{HASH}"],
                assessment_outcome=outcome,
            )
            s.add(
                FindingRecord(
                    run_id=uuid.UUID(run_id),
                    requirement_id=f.requirement_id,
                    behavior_id=f.behavior_id,
                    verdict=f.verdict,
                    category=f.category,
                    severity=f.severity,
                    payload=f.model_dump(mode="json"),
                )
            )


async def _stored(
    engine: AsyncEngine, slug: str, repo: str | None = None
) -> list[tuple[str, str, str]]:
    """そのワークスペースのプロジェクト（既定は `e2e/<slug>`）の確定。"""
    pid = await project_id_of(engine, repo or f"e2e/{slug}")
    async with async_sessionmaker(engine)() as s:
        return [
            (a.fingerprint, a.disposition, a.actor) for a in await list_attestations(s, slug, pid)
        ]


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
    run_id = await _pr_run(ctx, ctx.repo)
    await _seed_targets(engine, run_id, "R:i:1")
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {
        "fingerprint": "R:i:1",
        "content_hash": HASH,
        "disposition": "attested",
        "run_id": run_id,
    }
    assert (await ctx.c.post(url, json=body, headers=MEMBER)).status_code == 403
    assert (await ctx.c.post(url, json=body, headers=STRANGER)).status_code == 404
    bad = {**body, "content_hash": "sha256:xyz"}
    assert (await ctx.c.post(url, json=bad, headers=OWNER)).status_code == 422
    # run_id は必須（#114）
    no_run = {k: v for k, v in body.items() if k != "run_id"}
    assert (await ctx.c.post(url, json=no_run, headers=OWNER)).status_code == 422
    ok = await ctx.c.post(url, json=body, headers=OWNER)
    assert ok.status_code == 200, ok.text
    assert ok.json()["attestation"]["actor"] == "owner1"
    assert ok.json()["attestation"]["project_id"] == str(await project_id_of(engine, ctx.repo))
    assert ok.json()["recheck_run_id"] is not None
    ack = {**body, "disposition": "acknowledged_violation"}
    assert (await ctx.c.post(url, json=ack, headers=APPROVER)).status_code == 200
    assert await _stored(engine, ctx.slug) == [("R:i:1", "acknowledged_violation", "approver1")]
    assert await _stored(engine, ctx.other) == []
    assert await _audit(engine, ctx.slug) == [
        ("member1", "rejected"),
        ("owner1", "attested"),
        ("approver1", "acknowledged_violation"),
    ]


async def test_rest_attest_rechecks_the_pr_head(ctx: Ctx, engine: AsyncEngine) -> None:
    run_id = await _pr_run(ctx, ctx.repo)
    other_run = await _pr_run(ctx, ctx.other_repo)
    await _seed_targets(engine, run_id, "R:i:1")
    await _seed_targets(engine, other_run, "R:i:1")
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
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:2")
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
    await _seed_targets(engine, res["run_id"], "R:i:2")  # 再検査の run にも同じ指摘
    assert await _stored(engine, ctx.slug) == [("R:i:2", "attested", GH_APPROVER)]

    raw, headers = signed(
        comment_payload(ctx.repo, 1, f"/speclino attest R:i:2 {HASH} ack", "gh-approver"),
        "issue_comment",
    )
    await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    assert await _stored(engine, ctx.slug) == [("R:i:2", "acknowledged_violation", GH_APPROVER)]

    raw, headers = signed(
        comment_payload(ctx.repo, 1, "/speclino attest R:i:2 not-a-hash", "gh-approver"),
        "issue_comment",
    )
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is False


async def test_pr_comment_attest_never_falls_back_to_login(ctx: Ctx, engine: AsyncEngine) -> None:
    """承認者と同じログイン名でも ID が違えば確定できない（S61 の ID 照合。名前に戻さない）。"""
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:2")
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    payload = comment_payload(
        ctx.repo, 1, f"/speclino attest R:i:2 {HASH}", "gh-approver", user_id=4242
    )
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is False
    payload["comment"]["user"].pop("id")
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is False
    assert await _stored(engine, ctx.slug) == []


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


# 承認者の照合は ID（`set_project_approvers` が `login_id` で保存する）なので、同じ ID を使う
DEV_ID, OTHER_ID = login_id("dev"), login_id("gh-approver")
GH_DEV = f"github:{DEV_ID}"


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
    await _seed_targets(engine, run_id, "R:i:1", "R:i:2", "R:i:3")
    linked = {"approver1": DEV_ID, "owner1": OTHER_ID}
    assert await _attest_via_service(engine, ctx, run_id, "approver1", "R:i:1", linked) is True
    assert await _attest_via_service(engine, ctx, run_id, "owner1", "R:i:2", linked) is False
    # 未連携は照合不可（null）。黙って false にしない
    assert await _attest_via_service(engine, ctx, run_id, "dev", "R:i:3", linked) is None
    audit = await _audit_detail(engine, ctx.slug)
    assert [(a, m) for a, m, _ in audit] == [
        ("approver1", True),
        ("owner1", False),
        ("dev", None),
    ]


async def test_bindings_attestation_is_rejected_over_rest(ctx: Ctx, engine: AsyncEngine) -> None:
    """裁定 35: 対応表・免除・規約の変更の確定は PR コメントだけ。REST は 403、拒否は監査に残る。"""
    run_id = await _pr_run_by(ctx, DEV_ID)
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {
        "fingerprint": f"KNOWLEDGE-BINDINGS-001:{HASH}",
        "content_hash": HASH,
        "disposition": "attested",
        "run_id": run_id,
    }
    res = await ctx.c.post(url, json=body, headers=OWNER)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "bindings_attest_requires_pr_comment"
    assert "PR コメント" in res.json()["error"]["message"]
    assert await _stored(engine, ctx.slug) == []
    assert await _audit(engine, ctx.slug) == [("owner1", "rejected")]


async def test_bindings_attestation_via_pr_comment_is_accepted(
    ctx: Ctx, engine: AsyncEngine
) -> None:
    fp = f"KNOWLEDGE-BINDINGS-001:{HASH}"
    await _seed_targets(engine, await _pr_run_by(ctx, DEV_ID), fp)
    await set_project_approvers(engine, ctx.repo, ["dev"])
    payload = comment_payload(ctx.repo, 1, f"/speclino attest {fp} {HASH}", "dev")
    payload["comment"]["user"]["id"] = DEV_ID
    payload["issue"]["user"] = {"login": "dev", "id": DEV_ID}
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is True and res["commands"][0]["self_attested"] is True
    assert await _stored(engine, ctx.slug) == [(fp, "attested", GH_DEV)]
    # 作成者自身の確定は拒否せず、監査に警告を残す
    ((actor, mark, warning),) = await _audit_detail(engine, ctx.slug)
    assert (actor, mark) == (GH_DEV, True) and warning is not None
    # ログイン名は識別に使わず、監査の詳細に残す
    async with engine.connect() as conn:
        login = (
            await conn.execute(
                sql_text(
                    "SELECT detail->>'actor_login' FROM audit_events "
                    "WHERE action = 'attestation.record' AND target LIKE :t"
                ),
                {"t": f"{ctx.slug}:%"},
            )
        ).scalar_one()
    assert login == "dev"


async def test_self_attestation_is_null_without_author_id(ctx: Ctx, engine: AsyncEngine) -> None:
    run_id = await _pr_run_by(ctx, None)
    await _seed_targets(engine, run_id, "R:i:4", "R:i:5")
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
    await _seed_targets(engine, await _pr_run_by(ctx, DEV_ID), "R:i:6", "R:i:7", "R:i:8")
    await set_project_approvers(engine, ctx.repo, ["dev", "gh-approver"])
    cases = (("dev", DEV_ID, "R:i:6", True), ("gh-approver", OTHER_ID, "R:i:7", False))
    for login, user_id, fp, expected in cases:
        payload = comment_payload(ctx.repo, 1, f"/speclino attest {fp} {HASH}", login)
        payload["comment"]["user"]["id"] = user_id
        payload["issue"]["user"] = {"login": "dev", "id": DEV_ID}  # PR の作成者
        raw, headers = signed(payload, "issue_comment")
        res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
        assert res["commands"][0]["self_attested"] is expected, res
        await _seed_targets(engine, res["run_id"], "R:i:6", "R:i:7", "R:i:8")
    # ID の無いペイロードは照合不可
    payload = comment_payload(ctx.repo, 1, f"/speclino attest R:i:8 {HASH}", "dev")
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["self_attested"] is None
    pid = await project_id_of(engine, ctx.repo)
    async with async_sessionmaker(engine)() as s:
        marks = {a.fingerprint: a.self_attested for a in await list_attestations(s, ctx.slug, pid)}
    assert marks == {"R:i:6": True, "R:i:7": False, "R:i:8": None}


async def test_pr_comment_cannot_attest_another_prs_finding(ctx: Ctx, engine: AsyncEngine) -> None:
    """承認者 X が自分の PR #1 の指紋を、他人の PR #2 へのコメントで確定 → 拒否・記録なし。"""
    own = _pr_payload_by(ctx, 1, DEV_ID)
    raw, headers = signed(own, "pull_request")
    run1 = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()["run_id"]
    await _seed_targets(engine, run1, "R:i:own")
    raw, headers = signed(_pr_payload_by(ctx, 2, OTHER_ID), "pull_request")
    await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    await set_project_approvers(engine, ctx.repo, ["dev"])
    payload = comment_payload(ctx.repo, 2, f"/speclino attest R:i:own {HASH}", "dev")
    payload["comment"]["user"]["id"] = DEV_ID
    payload["issue"]["user"] = {"login": "someone", "id": OTHER_ID}
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["status"] == "handled"  # 再検査も作らない
    assert res["commands"][0] == {
        "kind": "attest",
        "accepted": False,
        "reason": "この PR の検査に無い指紋",
    }
    assert await _stored(engine, ctx.slug) == []
    assert await _audit(engine, ctx.slug) == [(GH_DEV, "rejected")]
    # 自分の PR #1 へのコメントなら受理される
    payload["issue"] = {"number": 1, "pull_request": {"url": "x"}, "user": {"id": DEV_ID}}
    raw, headers = signed(payload, "issue_comment")
    res = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["commands"][0]["accepted"] is True and res["commands"][0]["self_attested"] is True


def _pr_payload_by(ctx: Ctx, number: int, author_id: int) -> dict[str, object]:
    payload = pr_payload(ctx.repo, ctx.head, ctx.base, number)
    payload["pull_request"]["user"]["id"] = author_id  # type: ignore[index]
    return payload


async def test_rest_attest_requires_a_finding_of_that_run(ctx: Ctx, engine: AsyncEngine) -> None:
    """#114: その run の保存済みの確定できる指摘でなければ 404。記録せず、拒否は監査に残す。"""
    run_id = await _pr_run(ctx, ctx.repo)
    await _seed_targets(engine, run_id, "R:i:1")
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {
        "fingerprint": "R:i:9",
        "content_hash": HASH,
        "disposition": "attested",
        "run_id": run_id,
    }
    res = await ctx.c.post(url, json=body, headers=OWNER)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "attestation_target_not_found"
    # 指紋が合っても hash が違えば同じ
    other_hash = {**body, "fingerprint": "R:i:1", "content_hash": "sha256:" + "c" * 64}
    assert (await ctx.c.post(url, json=other_hash, headers=OWNER)).status_code == 404
    # 存在しない run も 404
    missing = {**body, "fingerprint": "R:i:1", "run_id": str(uuid.uuid4())}
    assert (await ctx.c.post(url, json=missing, headers=OWNER)).status_code == 404
    assert await _stored(engine, ctx.slug) == []
    assert await _audit(engine, ctx.slug) == [("owner1", "rejected")] * 3


async def test_attestation_does_not_cross_projects(ctx: Ctx, engine: AsyncEngine) -> None:
    """#114: 同じワークスペースの別プロジェクトに同じ指紋があっても、確定は run の
    プロジェクトだけ。"""
    second = f"e2e/{ctx.slug}-second"
    await register_repo(engine, second, {"owner1": "owner"}, team=ctx.slug)
    run_a = await _pr_run(ctx, ctx.repo)
    await _seed_targets(engine, run_a, "R:i:x")
    url = f"/api/workspaces/{ctx.slug}/attestations"
    body = {
        "fingerprint": "R:i:x",
        "content_hash": HASH,
        "disposition": "attested",
        "run_id": run_a,
    }
    assert (await ctx.c.post(url, json=body, headers=OWNER)).status_code == 200
    assert await _stored(engine, ctx.slug) == [("R:i:x", "attested", "owner1")]
    assert await _stored(engine, ctx.slug, second) == []


# ---------------------------------------------------------------- PR の Approve による確定


def _review_payload(
    ctx: Ctx,
    login: str,
    *,
    commit_id: str | None = None,
    state: str = "approved",
    number: int = 1,
    pr_author_id: int | None = 777_777,
) -> dict[str, Any]:
    pr_user: dict[str, Any] = {"login": "author"}
    if pr_author_id is not None:
        pr_user["id"] = pr_author_id
    return {
        "action": "submitted",
        "installation": {"id": 1},
        "repository": {"full_name": ctx.repo, "id": repo_id_of(ctx.repo)},
        "review": {
            "state": state,
            "commit_id": commit_id or ctx.head,
            "html_url": f"https://github.com/{ctx.repo}/pull/{number}#pullrequestreview-1",
            "user": {"login": login, "id": login_id(login)},
        },
        "pull_request": {"number": number, "user": pr_user},
    }


async def _post_review(ctx: Ctx, payload: dict[str, Any], delivery: str | None = None) -> Any:
    raw, headers = signed(payload, "pull_request_review", delivery)
    res = await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _review_audit(engine: AsyncEngine, repo: str) -> list[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            sql_text(
                "SELECT decision, detail->>'reason' FROM audit_events "
                "WHERE action = 'attestation.bulk' AND target LIKE :t ORDER BY ts"
            ),
            {"t": f"{repo}#%"},
        )
        return [(r[0], r[1]) for r in rows]


async def test_pr_review_approval_attests_the_runs_reviews(ctx: Ctx, engine: AsyncEngine) -> None:
    """承認者の Approve で、最新の run の確定できる要確認をすべて確定し、再検査する。"""
    bindings = f"KNOWLEDGE-BINDINGS-001:{HASH}"
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1", bindings)
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    res = await _post_review(ctx, _review_payload(ctx, "gh-approver"))
    assert res["status"] == "queued" and res["accepted"] is True, res
    assert sorted(res["attested"]) == sorted(["R:i:1", bindings])
    assert res["self_attested"] == [False, False]
    assert sorted(await _stored(engine, ctx.slug)) == sorted(
        [("R:i:1", "attested", GH_APPROVER), (bindings, "attested", GH_APPROVER)]
    )
    # 経路は監査の via で区別する（ログイン名も残す）
    async with engine.connect() as conn:
        vias = (
            await conn.execute(
                sql_text(
                    "SELECT detail->>'via', detail->>'actor_login' FROM audit_events "
                    "WHERE action = 'attestation.record' AND target LIKE :t"
                ),
                {"t": f"{ctx.slug}:%"},
            )
        ).all()
    assert {(v.split("#")[0], login) for v, login in vias} == {
        (f"pr_review:https://github.com/{ctx.repo}/pull/1", "gh-approver")
    }
    # 再検査の run が作られ、ゲートのコメント・Check の更新（worker）まで進む
    detail = (await ctx.c.get(f"/api/review-runs/{res['run_id']}", headers=OWNER)).json()
    assert detail["trigger"] == "pr_review" and detail["head_sha"] == ctx.head
    assert detail["exec_state"] == "completed"
    assert await _review_audit(engine, ctx.repo) == [("accepted", "承認者の一括の確定")]


async def test_pr_review_on_old_commit_does_nothing(ctx: Ctx, engine: AsyncEngine) -> None:
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    res = await _post_review(ctx, _review_payload(ctx, "gh-approver", commit_id=ctx.base))
    assert res["status"] == "handled" and res["accepted"] is False
    assert res["reason"] == "Approve したコミットが PR の最新の検査の head と違う"
    assert await _stored(engine, ctx.slug) == []
    assert await _review_audit(engine, ctx.repo) == [("rejected", res["reason"])]


async def test_pr_review_by_non_approver_does_nothing(ctx: Ctx, engine: AsyncEngine) -> None:
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    res = await _post_review(ctx, _review_payload(ctx, "stranger"))
    assert res["status"] == "handled" and res["accepted"] is False
    # 承認者と同じログイン名でも ID が違えば通らない
    payload = _review_payload(ctx, "gh-approver")
    payload["review"]["user"]["id"] = 4242
    res = await _post_review(ctx, payload)
    assert res["accepted"] is False
    assert await _stored(engine, ctx.slug) == []
    assert [d for d, _ in await _review_audit(engine, ctx.repo)] == ["rejected", "rejected"]


async def test_pr_review_other_states_are_ignored(ctx: Ctx, engine: AsyncEngine) -> None:
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    for state in ("commented", "changes_requested"):
        res = await _post_review(ctx, _review_payload(ctx, "gh-approver", state=state))
        assert res["status"] == "ignored"
    dismissed = {**_review_payload(ctx, "gh-approver"), "action": "dismissed"}
    assert (await _post_review(ctx, dismissed))["status"] == "ignored"
    assert await _stored(engine, ctx.slug) == []


async def test_pr_review_marks_self_attestation(ctx: Ctx, engine: AsyncEngine) -> None:
    """自己確定の印は PR コメントと同じ規則（GitHub の ID で照合、照合不可は null）。"""
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["dev"])
    res = await _post_review(ctx, _review_payload(ctx, "dev", pr_author_id=DEV_ID))
    assert res["self_attested"] == [True]
    # 作成者の ID が無い（run にも無い）なら照合不可
    await _seed_targets(engine, res["run_id"], "R:i:2")
    res = await _post_review(ctx, _review_payload(ctx, "dev", pr_author_id=None))
    assert res["attested"] == ["R:i:2"] and res["self_attested"] == [None]


async def test_pr_review_approval_is_idempotent(ctx: Ctx, engine: AsyncEngine) -> None:
    run_id = await _pr_run(ctx, ctx.repo)
    await _seed_targets(engine, run_id, "R:i:1", "R:i:2")
    await set_project_approvers(engine, ctx.repo, ["gh-approver"])
    # 問題として記録した（ack）ものは Approve で覆さない
    ack = comment_payload(ctx.repo, 1, f"/speclino attest R:i:2 {HASH} ack", "gh-approver")
    raw, headers = signed(ack, "issue_comment")
    rerun = (await ctx.c.post("/webhooks/github", content=raw, headers=headers)).json()["run_id"]
    await _seed_targets(engine, rerun, "R:i:1", "R:i:2")
    payload = _review_payload(ctx, "gh-approver")
    delivery = uuid.uuid4().hex
    first = await _post_review(ctx, payload, delivery=delivery)
    assert first["attested"] == ["R:i:1"]
    # 同じ配信の再送は重複として捨てる
    assert (await _post_review(ctx, payload, delivery=delivery))["status"] == "duplicate"
    # 別の Approve（再検査の run に同じ指摘があり、確定が効いている）でも、確定済みは記録し直さず、
    # 再検査もしない
    await _seed_targets(engine, first["run_id"], "R:i:1", "R:i:2")
    await _mark_applied(engine, first["run_id"], "R:i:1", "R:i:2")
    again = await _post_review(ctx, payload)
    assert again["status"] == "handled" and again["attested"] == []
    assert sorted(await _stored(engine, ctx.slug)) == [
        ("R:i:1", "attested", GH_APPROVER),
        ("R:i:2", "acknowledged_violation", GH_APPROVER),
    ]
    assert [d for d, _ in await _review_audit(engine, ctx.repo)] == ["accepted", "ignored"]


# ---------------------------------------------------------------- 引数なしの `/speclino attest`


async def _comment(ctx: Ctx, body: str, login: str, author_id: int | None = None) -> Any:
    payload = comment_payload(ctx.repo, 1, body, login)
    if author_id is not None:
        payload["issue"]["user"] = {"login": "author", "id": author_id}
    raw, headers = signed(payload, "issue_comment")
    res = await ctx.c.post("/webhooks/github", content=raw, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def test_bare_attest_comment_attests_all_reviews(ctx: Ctx, engine: AsyncEngine) -> None:
    """1 人運用（作成者＝承認者）でも、引数なしのコメントで最新の検査の要確認をまとめて確定する。"""
    bindings = f"KNOWLEDGE-BINDINGS-001:{HASH}"
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1", bindings)
    await set_project_approvers(engine, ctx.repo, ["dev"])
    res = await _comment(ctx, "  /speclino attest  ", "dev", author_id=DEV_ID)
    assert res["status"] == "queued", res  # 確定の後は再検査
    (cmd,) = res["commands"]
    assert cmd["accepted"] is True and sorted(cmd["attested"]) == sorted(["R:i:1", bindings])
    assert cmd["self_attested"] == [True, True]
    assert sorted(await _stored(engine, ctx.slug)) == sorted(
        [("R:i:1", "attested", GH_DEV), (bindings, "attested", GH_DEV)]
    )
    # 自己確定の対応表の変更には警告が残り、経路は via で区別する
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sql_text(
                    "SELECT detail->>'via', detail->>'warning' FROM audit_events "
                    "WHERE action = 'attestation.record' AND target LIKE :t"
                ),
                {"t": f"{ctx.slug}:%"},
            )
        ).all()
    assert {v for v, _ in rows} == {"pr_comment_all:https://c/1"}
    assert sum(1 for _, w in rows if w is not None) == 1
    detail = (await ctx.c.get(f"/api/review-runs/{res['run_id']}", headers=OWNER)).json()
    assert detail["trigger"] == "comment" and detail["exec_state"] == "completed"


async def test_bare_attest_waits_for_the_latest_head(ctx: Ctx, engine: AsyncEngine) -> None:
    """最新の run が完了していない（検査中・未検査のコミットがある）なら確定しない。"""
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["dev"])
    pid = await project_id_of(engine, ctx.repo)
    from speclino.db.repositories import ReviewRunRepository

    async with async_sessionmaker(engine)() as s, s.begin():
        await ReviewRunRepository(s).create(
            project_id=pid,
            pr_number=1,
            head_sha="f" * 40,
            base_sha=ctx.base,
            trigger="pull_request.synchronize",
            parent_run_id=None,
            pr_author_id=None,
        )
    res = await _comment(ctx, "/speclino attest", "dev")
    assert res["status"] == "handled"
    assert res["commands"][0]["accepted"] is False
    assert "まだ完了していない" in res["commands"][0]["reason"]
    assert await _stored(engine, ctx.slug) == []


async def test_bare_attest_by_non_approver_does_nothing(ctx: Ctx, engine: AsyncEngine) -> None:
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["dev"])
    res = await _comment(ctx, "/speclino attest", "stranger")
    assert res["status"] == "handled" and res["commands"][0]["accepted"] is False
    assert await _stored(engine, ctx.slug) == []


async def test_bare_attest_is_idempotent_and_keeps_ack(ctx: Ctx, engine: AsyncEngine) -> None:
    await _seed_targets(engine, await _pr_run(ctx, ctx.repo), "R:i:1", "R:i:2")
    await set_project_approvers(engine, ctx.repo, ["dev"])
    res = await _comment(ctx, f"/speclino attest R:i:2 {HASH} ack", "dev")
    await _seed_targets(engine, res["run_id"], "R:i:1", "R:i:2")
    res = await _comment(ctx, "/speclino attest", "dev")
    assert res["commands"][0]["attested"] == ["R:i:1"]
    await _seed_targets(engine, res["run_id"], "R:i:1", "R:i:2")
    await _mark_applied(engine, res["run_id"], "R:i:1", "R:i:2")
    again = await _comment(ctx, "/speclino attest", "dev")
    assert again["status"] == "handled" and again["commands"][0]["attested"] == []
    assert sorted(await _stored(engine, ctx.slug)) == [
        ("R:i:1", "attested", GH_DEV),
        ("R:i:2", "acknowledged_violation", GH_DEV),
    ]


async def _mark_applied(engine: AsyncEngine, run_id: str, *fingerprints: str) -> None:
    """run の manifest に確定が効いた（検査に反映された）印を付ける。"""
    applied = [{"fingerprint": fp, "content_hash": HASH} for fp in fingerprints]
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "UPDATE review_runs SET manifest = jsonb_set(coalesce(manifest, '{}'::jsonb), "
                "'{attestations}', cast(:a as jsonb)) WHERE id = :id"
            ),
            {"a": json.dumps(applied), "id": uuid.UUID(run_id)},
        )


async def test_bulk_attest_rechecks_when_attestation_is_not_applied(
    ctx: Ctx, engine: AsyncEngine
) -> None:
    """確定のあとの再検査の投入に失敗した（確定は残り、検査に反映されていない）とき、再送や
    2 回目の依頼で新しい確定が 0 件でも再検査を作り直す。"""
    run_id = await _pr_run(ctx, ctx.repo)
    await _seed_targets(engine, run_id, "R:i:1")
    await set_project_approvers(engine, ctx.repo, ["dev"])
    # 確定だけが保存され、再検査が走らなかった状態
    async with async_sessionmaker(engine)() as s, s.begin():
        await attest(
            s,
            fingerprint="R:i:1",
            content_hash=HASH,
            disposition="attested",
            actor=GH_DEV,
            workspace=ctx.slug,
            project_id=await project_id_of(engine, ctx.repo),
        )
    res = await _comment(ctx, "/speclino attest", "dev")
    assert res["status"] == "queued", res
    assert res["commands"][0]["attested"] == []
    # 反映済みなら作らない
    await _seed_targets(engine, res["run_id"], "R:i:1")
    await _mark_applied(engine, res["run_id"], "R:i:1")
    again = await _comment(ctx, "/speclino attest", "dev")
    assert again["status"] == "handled"


async def test_bulk_attest_never_overwrites_a_concurrent_ack(ctx: Ctx, engine: AsyncEngine) -> None:
    """一括の確定の書込みは「無ければ入れる」だけ。直前に別の経路で保存された ack を覆さない。"""
    pid = await project_id_of(engine, ctx.repo)
    async with async_sessionmaker(engine)() as s, s.begin():
        await attest(
            s,
            fingerprint="R:i:1",
            content_hash=HASH,
            disposition="acknowledged_violation",
            actor=GH_DEV,
            workspace=ctx.slug,
            project_id=pid,
        )
    async with async_sessionmaker(engine)() as s, s.begin():
        saved = await attest_if_absent(
            s,
            fingerprint="R:i:1",
            content_hash=HASH,
            actor=GH_APPROVER,
            workspace=ctx.slug,
            project_id=pid,
            via="pr_comment_all:x",
            self_attested=False,
            actor_login="gh-approver",
        )
    assert saved is None
    assert await _stored(engine, ctx.slug) == [("R:i:1", "acknowledged_violation", GH_DEV)]
