"""API 経由の E2E: Webhook → run 実行 → 確認 API → approve-spec コメント → 再検証。"""

from __future__ import annotations

import hashlib
import hmac
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
from speclino.db.repositories.mcp_tokens import McpTokenRepository
from speclino.domain.a2a import TokenIdentity
from speclino.reporter.check_report import COMMENT_MARKER
from speclino.services import agent_supplements, mcp_tokens
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from speclino.spec import load_spec_ir
from speclino.spec.spec_validator import rule_content_hash
from tests.integration.services.conftest import register_repo, repo_id_of, set_project_approvers
from tests.integration.services.test_review_run_service import make_demo_repo


def _base_rule(rule_id: str):  # type: ignore[no-untyped-def]
    """base（examples/orders の承認済み Spec IR）から Rule を引く。"""
    spec = load_spec_ir(
        Path(__file__).resolve().parents[3] / "examples/orders/specs/ir/cancel_order.spec-ir.json"
    )
    return next(r for r in spec.rules if r.id == rule_id)


_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]
SECRET = "webhook-secret"


def signed(
    body: dict[str, Any], event: str, delivery: str | None = None
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or uuid.uuid4().hex,
        "Content-Type": "application/json",
    }


def pr_payload(
    repo: str, head: str, base: str, number: int, action: str = "opened"
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "installation": {"id": 1},
        "repository": {"full_name": repo, "id": repo_id_of(repo)},
        "pull_request": {
            "number": number,
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "user": {"login": "dev"},
            "head": {"sha": head, "repo": {"full_name": repo}},
            "base": {"sha": base},
        },
    }


def comment_payload(repo: str, number: int, body: str, login: str) -> dict[str, Any]:
    return {
        "action": "created",
        "installation": {"id": 1},
        "repository": {"full_name": repo, "id": repo_id_of(repo)},
        "issue": {"number": number, "pull_request": {"url": "x"}},
        "comment": {"body": body, "user": {"login": login}, "html_url": "https://c/1"},
    }


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost]]:
    repo = f"e2e/api-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, repo, {"dev": "member", "judge": "member"})
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all", github_webhook_secret=SECRET)
    service = ReviewRunService(
        factory,
        host,
        queue,
        FileSystemBlobStore(tmp_path / "blobs"),
        ReviewRunConfig(agent_enabled=False),
    )
    holder["svc"] = service
    state = AppState(
        settings, engine, factory, host, queue, FileSystemBlobStore(tmp_path / "blobs"), service
    )
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c, repo, base, head, head2, host


async def test_webhook_to_api_flow(
    client: tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost],
) -> None:
    c, repo, base, head, _, host = client
    # 署名不正 → 401
    raw, headers = signed(pr_payload(repo, head, base, 1), "pull_request")
    bad = await c.post(
        "/webhooks/github", content=raw, headers={**headers, "X-Hub-Signature-256": "sha256=00"}
    )
    assert bad.status_code == 401
    # 正常
    res = await c.post("/webhooks/github", content=raw, headers=headers)
    assert res.status_code == 200 and res.json()["status"] == "queued", res.text
    run_id = res.json()["run_id"]
    # 同じ delivery の再送は重複として無視
    dup = await c.post("/webhooks/github", content=raw, headers=headers)
    assert dup.json()["status"] == "duplicate"
    # 確認 API
    detail = (await c.get(f"/api/review-runs/{run_id}")).json()
    assert detail["exec_state"] == "completed" and detail["gate_status"] == "fail"
    assert any(
        f["requirement_id"] == "R-CANCEL-04" and f["verdict"] == "VIOLATED"
        for f in detail["findings"]
    )
    events = (await c.get(f"/api/review-runs/{run_id}/events")).json()
    assert [e["type"] for e in events][0] == "state" and events[-1]["payload"][
        "publish"
    ] == "publish"
    listing = (await c.get("/api/review-runs")).json()
    assert listing[0]["id"] == run_id and listing[0]["repo_full_name"] == repo
    review = await c.get(f"/api/review-runs/{run_id}/artifacts/review.md")
    assert review.status_code == 200 and "R-CANCEL-04" in review.text
    assert (await c.get(f"/api/review-runs/{run_id}/artifacts/../x")).status_code in (400, 404)
    assert host.checks[1]["conclusion"] == "failure"
    # 会話タブに判定の要約コメントが 1 件出る（Checks タブを開かなくても読める）
    gate_comments = [c for c in host.comments if COMMENT_MARKER in c["body"]]
    assert len(gate_comments) == 1, host.comments
    body = gate_comments[0]["body"]
    assert "SpecLino / gate: 失敗" in body
    assert "R-CANCEL-04" in body and "inventory_restore_command" in body
    # S28: base（正しい実装）との意味差分が Check の要約と成果物に出る
    assert "新規違反 1 / 既存違反 0 / 解消 0" in host.checks[1]["summary"]
    diff = (await c.get(f"/api/review-runs/{run_id}/artifacts/semantic-diff.json")).json()
    assert diff["base_available"] and diff["counts"]["new_violation"] == 1
    assert detail["manifest"]["semantic_diff"]["counts"]["new_violation"] == 1


async def test_approve_spec_comment_triggers_rerun(
    client: tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost], engine: AsyncEngine
) -> None:
    c, repo, base, _, head2, host = client
    raw, headers = signed(pr_payload(repo, head2, base, 2), "pull_request")
    first = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
    await set_project_approvers(engine, repo, ["approver"])
    assert (await c.get(f"/api/review-runs/{first['run_id']}")).json()["gate_status"] == "fail"
    base_hash = rule_content_hash(_base_rule("R-CANCEL-04"))
    # 承認者でない人のコマンドは拒否（監査に残る）
    raw, headers = signed(
        comment_payload(repo, 2, f"/speclino approve-spec removed:{base_hash}", "stranger"),
        "issue_comment",
    )
    res = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["status"] == "handled" and res["commands"][0]["accepted"] is False
    # 承認者のコマンド → 承認記録 → 再検証 → 通過
    raw, headers = signed(
        comment_payload(repo, 2, f"LGTM\n/speclino approve-spec removed:{base_hash}", "approver"),
        "issue_comment",
    )
    res = (await c.post("/webhooks/github", content=raw, headers=headers)).json()
    assert res["status"] == "queued", res
    assert (await c.get(f"/api/review-runs/{res['run_id']}")).json()["gate_status"] == "pass"
    assert host.checks[max(host.checks)]["conclusion"] == "success"
    # run を作り直しても project の承認者は消えない（以前は create_run が {} で上書きした）
    async with engine.connect() as conn:
        settings = (
            await conn.execute(
                sql_text("SELECT settings FROM projects WHERE repo_full_name = :r"), {"r": repo}
            )
        ).scalar_one()
    assert settings["approvers"] == ["approver"]


async def test_manual_start_and_healthz(
    client: tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost],
) -> None:
    c, repo, base, head, _, _ = client
    assert (await c.get("/health")).json()["status"] == "ok"
    res = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers={"X-Dev-User": "judge"},
    )
    assert res.status_code == 202
    detail = (await c.get(f"/api/review-runs/{res.json()['run_id']}")).json()
    assert detail["trigger"] == "manual:judge" and detail["gate_status"] == "fail"


async def test_non_members_see_nothing_and_cannot_start_runs(
    client: tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost], engine: AsyncEngine
) -> None:
    """S53: 所属ワークスペースに登録されていない run は、一覧に出ず、詳細も 404。"""
    c, repo, base, head, _, _ = client
    raw, headers = signed(pr_payload(repo, head, base, 5), "pull_request")
    run_id = (await c.post("/webhooks/github", content=raw, headers=headers)).json()["run_id"]
    outsider = {"X-Dev-User": "outsider"}

    assert (await c.get("/api/review-runs", headers=outsider)).json() == []
    for path in (
        f"/api/review-runs/{run_id}",
        f"/api/review-runs/{run_id}/events",
        f"/api/review-runs/{run_id}/artifacts/review.md",
    ):
        res = await c.get(path, headers=outsider)
        assert res.status_code == 404, (path, res.text)
    assert (await c.post(f"/api/review-runs/{run_id}/rerun", headers=outsider)).status_code == 404
    start = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers=outsider,
    )
    assert start.status_code == 404
    # 存在しないリポジトリも同じ 404（存在を漏らさない）。API から project は作らない
    missing = await c.post(
        "/api/review-runs",
        json={"repo_full_name": "e2e/nope", "head_sha": head, "base_sha": base},
    )
    assert missing.status_code == 404
    # head_sha は URL のパスに入るので、`..` を含む形は入力の検証で拒否する
    traversal = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": "../../other/repo/tarball/main"},
    )
    assert traversal.status_code == 422
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                sql_text("SELECT count(*) FROM projects WHERE repo_full_name = 'e2e/nope'")
            )
        ).scalar_one()
    assert count == 0

    # 別のワークスペースのメンバーでも見えない
    await register_repo(engine, "e2e/other-ws-repo", {"outsider": "owner"}, team="e2e-other")
    assert (await c.get(f"/api/review-runs/{run_id}", headers=outsider)).status_code == 404

    # メンバーからは見える。/api/me に所属が出る
    assert (await c.get(f"/api/review-runs/{run_id}")).status_code == 200
    me = (await c.get("/api/me")).json()
    assert me["uid"] == "dev"
    assert {"slug": "e2e-ws", "name": None, "role": "member"} in me["workspaces"]


async def test_supplements_api_lists_own_workspace_and_hides_others(
    client: tuple[httpx.AsyncClient, str, str, str, str, LocalCodeHost], engine: AsyncEngine
) -> None:
    """S59: 補足の一覧は run が見える人にだけ返る。見えない run は get_run と同じ 404。"""
    c, repo, base, head, _, _ = client
    raw, headers = signed(pr_payload(repo, head, base, 6), "pull_request")
    run_id = uuid.UUID(
        (await c.post("/webhooks/github", content=raw, headers=headers)).json()["run_id"]
    )
    path = f"/api/review-runs/{run_id}/supplements"
    assert (await c.get(path)).json() == {"supplements": []}

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        token = await McpTokenRepository(s).create(
            team="e2e-ws",
            name="claude-code",
            token_hash=mcp_tokens.hash_token(mcp_tokens.TOKEN_PREFIX + uuid.uuid4().hex),
            created_by="t",
        )
        await agent_supplements.record(
            s,
            identity=TokenIdentity(team="e2e-ws", token_id=token.id),
            run_id=run_id,
            requirement_id="R-1",
            question="なぜ？",
            content="リトライで吸収",
            a2a_task_id="task-1",
        )

    res = await c.get(path)
    assert res.status_code == 200
    items = res.json()["supplements"]
    assert len(items) == 1
    assert items[0]["requirement_id"] == "R-1"
    assert items[0]["content"] == "リトライで吸収"
    assert items[0]["source"] == "claude-code"

    # 所属していない人からは、存在しない run と同じ 404
    outsider = {"X-Dev-User": "outsider"}
    assert (await c.get(path, headers=outsider)).status_code == 404
    await register_repo(engine, "e2e/other-ws-sup", {"outsider": "owner"}, team="e2e-other")
    assert (await c.get(path, headers=outsider)).status_code == 404
