"""project の設定・オンボーディング（S47）と、ワークスペースで絞った検査一覧の統合試験。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.models import AuditEvent
from speclino.ports.github_users import GitHubUserLookupError
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]


# GitHub のログイン名 → ユーザー ID（`GET /users/{login}` の代わり）
GITHUB_USERS = {"octo-cat": 583231, "hubot": 9919}


@dataclass
class _FakeUsers:
    ids: dict[str, int]
    down: bool = False

    async def user_id(self, login: str) -> int | None:
        if self.down:
            raise GitHubUserLookupError("rate limited")
        return self.ids.get(login)


def _as(uid: str) -> dict[str, str]:
    return {"X-Dev-User": uid}


@pytest.fixture
def users() -> _FakeUsers:
    return _FakeUsers(dict(GITHUB_USERS))


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine, tmp_path: Path, users: _FakeUsers
) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    settings = Settings(env="local", role="all")
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    host = LocalCodeHost(repos={})
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    state = AppState(settings, engine, factory, host, queue, blobs, service)
    state.github_users = users
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def _workspace(engine: AsyncEngine) -> tuple[str, str, uuid.UUID]:
    slug = f"e2e-set-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(
        engine, repo, {"own": "owner", "appr": "approver", "mem": "member"}, team=slug
    )
    return slug, repo, await project_id_of(engine, repo)


async def _add_run(
    engine: AsyncEngine, project_id: uuid.UUID, exec_state: str, error: str | None = None
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO review_runs (id, project_id, head_sha, trigger, exec_state, budget, "
                "next_event_seq, created_at) VALUES (:id, :pid, 'abc1234', 'test', "
                "CAST(:state AS exec_state), '{}', 2, clock_timestamp())"
            ),
            {"id": run_id, "pid": project_id, "state": exec_state},
        )
        if error is not None:
            await conn.execute(
                sql_text(
                    "INSERT INTO run_events (id, run_id, seq, type, payload) "
                    "VALUES (gen_random_uuid(), :rid, 1, 'state', CAST(:payload AS jsonb))"
                ),
                {"rid": run_id, "payload": f'{{"exec_state": "failed", "error": "{error}"}}'},
            )
    return run_id


# ------------------------------------------------------------------ 設定


async def test_member_reads_defaults_but_cannot_edit(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug, repo, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    res = await client.get(path, headers=_as("mem"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["can_edit"] is False and body["stored_invalid"] is False
    assert body["repo_full_name"] == repo
    assert body["settings"] == {
        "fail_on": ["VIOLATED", "UNKNOWN", "REVIEW"],
        "agent_enabled": True,
        "test_run_enabled": True,
        "approvers": [],
        "approver_ids": [],
    }
    assert (await client.get(path, headers=_as("appr"))).json()["can_edit"] is True

    denied = await client.put(
        path, json={**body["settings"], "agent_enabled": False}, headers=_as("mem")
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "not_an_approver"
    async with async_sessionmaker(engine)() as s:
        events = list(
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == "project.settings", AuditEvent.target == repo
                    )
                )
            ).scalars()
        )
    assert [(e.actor, e.decision) for e in events] == [("mem", "denied")]
    assert (await client.get(path, headers=_as("mem"))).json()["settings"]["agent_enabled"] is True


async def _settings_audit(engine: AsyncEngine, repo: str) -> list[AuditEvent]:
    async with async_sessionmaker(engine)() as s:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.action == "project.settings", AuditEvent.target == repo)
            .order_by(AuditEvent.ts)
        )
        return list((await s.execute(stmt)).scalars())


async def _stored(engine: AsyncEngine, pid: uuid.UUID) -> object:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                sql_text("SELECT settings FROM projects WHERE id = :id"), {"id": pid}
            )
        ).scalar_one()


async def test_approver_updates_other_settings_and_it_is_audited(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """approver は承認者以外の設定を変えられる（承認者を据え置いたままの全体の PUT）。"""
    slug, repo, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    new = {
        "fail_on": ["VIOLATED", "UNKNOWN", "REVIEW"],
        "agent_enabled": False,
        "test_run_enabled": False,
        "approvers": [],
        "approver_ids": [],
    }
    res = await client.put(path, json=new, headers=_as("appr"))
    assert res.status_code == 200, res.text
    assert res.json()["settings"] == new
    assert (await client.get(path, headers=_as("mem"))).json()["settings"] == new
    assert await _stored(engine, pid) == new
    [event] = await _settings_audit(engine, repo)
    assert event.actor == "appr" and event.decision == "updated"
    assert event.detail["after"] == new and event.detail["before"]["agent_enabled"] is True


async def test_approver_cannot_change_pr_approvers(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """PR の承認者（approve-spec / answer / attest を受け付ける人）を変えられるのは owner だけ。

    approver が自分や協力者のログイン名を足せると、仕様の承認や人の証拠を注入できてしまう。
    """
    slug, repo, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    before = (await client.get(path, headers=_as("appr"))).json()["settings"]
    attempt = {**before, "agent_enabled": False, "approvers": ["appr-gh"]}
    res = await client.put(path, json=attempt, headers=_as("appr"))
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "approvers_owner_only"
    assert await _stored(engine, pid) == {}, "拒否したら何も書かない（他の項目も）"
    [event] = await _settings_audit(engine, repo)
    assert (event.actor, event.decision) == ("appr", "denied")
    assert event.detail["reason"] == "approvers_owner_only"

    # GET の can_edit_approvers は owner だけ True
    assert (await client.get(path, headers=_as("appr"))).json()["can_edit_approvers"] is False
    assert (await client.get(path, headers=_as("own"))).json()["can_edit_approvers"] is True


async def test_owner_changes_pr_approvers_and_approver_keeps_them(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug, repo, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    with_approvers = {
        "fail_on": ["VIOLATED", "UNKNOWN", "REVIEW"],
        "agent_enabled": True,
        "test_run_enabled": True,
        "approvers": ["octo-cat"],
    }
    res = await client.put(path, json=with_approvers, headers=_as("own"))
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["approvers"] == ["octo-cat"]
    # 保存のとき、ログイン名から GitHub のユーザー ID を引いて一緒に保存する（S61）
    assert res.json()["settings"]["approver_ids"] == [583231]
    # 画面で確かめられるよう、ログイン名と ID の組で返す（改名後の取り違えに気づくため）
    assert res.json()["approvers"] == [{"login": "octo-cat", "id": 583231}]

    # approver が承認者を据え置いて他を変えるのは通る
    keep = {**with_approvers, "test_run_enabled": False}
    res = await client.put(path, json=keep, headers=_as("appr"))
    assert res.status_code == 200, res.text
    assert await _stored(engine, pid) == {**keep, "approver_ids": [583231]}
    # 承認者を消すのも変更なので拒否
    cleared = await client.put(path, json={**keep, "approvers": []}, headers=_as("appr"))
    assert cleared.status_code == 403
    assert [(e.actor, e.decision) for e in await _settings_audit(engine, repo)] == [
        ("own", "updated"),
        ("appr", "updated"),
        ("appr", "denied"),
    ]


async def test_unknown_github_login_is_rejected(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """ID を引けなかったログイン名は保存を断る（何も書かない）。"""
    slug, _, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    before = (await client.get(path, headers=_as("own"))).json()["settings"]
    res = await client.put(
        path, json={**before, "approvers": ["octo-cat", "ghost-user"]}, headers=_as("own")
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "unknown_github_login"
    assert "ghost-user" in res.json()["error"]["message"]
    assert await _stored(engine, pid) == {}


async def test_github_lookup_failure_is_503_and_writes_nothing(
    client: httpx.AsyncClient, engine: AsyncEngine, users: _FakeUsers
) -> None:
    """レート制限・通信の失敗で ID を引けなければ 503（保存しない）。"""
    slug, _, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    before = (await client.get(path, headers=_as("own"))).json()["settings"]
    users.down = True
    res = await client.put(path, json={**before, "approvers": ["octo-cat"]}, headers=_as("own"))
    assert res.status_code == 503, res.text
    assert res.json()["error"]["code"] == "github_lookup_failed"
    # 次の失敗を診断できるよう、GitHub 側の理由（応答コードなど）をメッセージに残す
    assert "rate limited" in res.json()["error"]["message"]
    assert await _stored(engine, pid) == {}


async def test_approver_ids_are_resolved_by_the_server_not_the_client(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """送られてきた `approver_ids` は信じない（ログイン名から引き直した値で上書きする）。"""
    slug, _, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    before = (await client.get(path, headers=_as("own"))).json()["settings"]
    res = await client.put(
        path,
        json={**before, "approvers": ["hubot"], "approver_ids": [1]},
        headers=_as("own"),
    )
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["approver_ids"] == [9919]


async def test_saving_keeps_known_ids_and_fills_legacy_settings(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """既に引いた ID は保ち（改名後に同じログイン名を取った別人の ID にすり替えない）、
    ID の無い既存の設定は次の保存で ID を補う。"""
    slug, _, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    stored = {
        "fail_on": ["VIOLATED", "UNKNOWN", "REVIEW"],
        "agent_enabled": True,
        "test_run_enabled": True,
        "approvers": ["octo-cat"],
        "approver_ids": [111],
    }
    await _put_stored(engine, pid, stored)
    res = await client.put(
        path, json={**stored, "approvers": ["octo-cat", "hubot"]}, headers=_as("own")
    )
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["approver_ids"] == [111, 9919]

    legacy = {k: v for k, v in stored.items() if k != "approver_ids"}
    await _put_stored(engine, pid, legacy)
    # ID の無い設定は、画面で保存し直しを促す（ID が無いあいだは誰も承認者にならない）
    view = (await client.get(path, headers=_as("mem"))).json()
    assert view["approver_ids_missing"] is True
    assert view["approvers"] == [{"login": "octo-cat", "id": None}]
    # approver（owner でない）の保存では ID を補わない。どの GitHub アカウントを承認者として
    # 結び付けるかは承認者の変更と同じく owner だけが決める（改名後のログイン名の取得者を
    # owner でない人が結び付けられないように）
    res = await client.put(path, json={**legacy, "agent_enabled": False}, headers=_as("appr"))
    assert res.status_code == 200, res.text
    assert res.json()["settings"].get("approver_ids", []) == []
    assert res.json()["approver_ids_missing"] is True
    # owner の保存で補う
    res = await client.put(path, json=legacy, headers=_as("own"))
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["approver_ids"] == [583231]
    assert res.json()["approver_ids_missing"] is False


async def _put_stored(engine: AsyncEngine, pid: uuid.UUID, settings: dict[str, object]) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sql_text("UPDATE projects SET settings = CAST(:s AS jsonb) WHERE id = :id"),
            {"s": json.dumps(settings), "id": pid},
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"fail_on": ["VIOLATED"]},
        {"approvers": ["not a login"]},
        {"agent_enabled": True, "surprise": 1},
    ],
)
async def test_invalid_settings_are_rejected(
    client: httpx.AsyncClient, engine: AsyncEngine, payload: dict[str, object]
) -> None:
    slug, _, pid = await _workspace(engine)
    res = await client.put(
        f"/api/workspaces/{slug}/projects/{pid}/settings", json=payload, headers=_as("own")
    )
    assert res.status_code == 422, res.text


async def test_outsiders_and_other_workspaces_get_404(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    slug, _, pid = await _workspace(engine)
    other, _, other_pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/projects/{pid}/settings"
    assert (await client.get(path, headers=_as("nobody"))).status_code == 404
    assert (await client.put(path, json={}, headers=_as("nobody"))).status_code == 404
    # 自分のワークスペースの slug で別ワークスペースの project を指しても 404
    cross = f"/api/workspaces/{slug}/projects/{other_pid}/settings"
    assert (await client.get(cross, headers=_as("own"))).status_code == 404
    assert (await client.put(cross, json={}, headers=_as("own"))).status_code == 404
    assert other != slug


# ------------------------------------------------------------------ オンボーディング


async def test_onboarding_follows_runs(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    slug, repo, pid = await _workspace(engine)
    path = f"/api/workspaces/{slug}/onboarding"

    def states(body: dict) -> dict[str, str]:  # type: ignore[type-arg]
        [project] = body["projects"]
        assert project["repo_full_name"] == repo
        return {s["id"]: s["state"] for s in project["steps"]}

    res = await client.get(path, headers=_as("mem"))
    assert res.status_code == 200, res.text
    assert res.json()["can_edit_settings"] is False
    assert states(res.json()) == {"app": "unknown", "speclino": "unknown", "first_run": "todo"}

    async with engine.begin() as conn:
        await conn.execute(
            sql_text("UPDATE projects SET installation_id = 7 WHERE id = :id"), {"id": pid}
        )
    await _add_run(
        engine, pid, "failed", "入力不正: domain.yaml が無い: /tmp/x/.speclino/domain.yaml"
    )
    body = (await client.get(path, headers=_as("own"))).json()
    assert body["can_edit_settings"] is True
    assert states(body) == {"app": "done", "speclino": "todo", "first_run": "todo"}

    await _add_run(engine, pid, "completed")
    body = (await client.get(path, headers=_as("mem"))).json()
    assert states(body) == {"app": "done", "speclino": "done", "first_run": "done"}

    assert (await client.get(path, headers=_as("nobody"))).status_code == 404


# ------------------------------------------------------------------ 検査一覧の絞り込み


async def test_review_runs_can_be_filtered_by_workspace(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """ほかのワークスペースの run が多くても、このワークスペースの run が押し出されない。"""
    mine = f"e2e-runs-{uuid.uuid4().hex[:8]}"
    busy = f"e2e-runs-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{mine}", {"multi": "member"}, team=mine)
    await register_repo(engine, f"e2e/{busy}", {"multi": "member"}, team=busy)
    mine_run = await _add_run(engine, await project_id_of(engine, f"e2e/{mine}"), "completed")
    busy_pid = await project_id_of(engine, f"e2e/{busy}")
    for _ in range(3):
        await _add_run(engine, busy_pid, "completed")

    unfiltered = await client.get("/api/review-runs?limit=2", headers=_as("multi"))
    assert str(mine_run) not in {r["id"] for r in unfiltered.json()}

    res = await client.get(f"/api/review-runs?limit=2&workspace={mine}", headers=_as("multi"))
    assert res.status_code == 200, res.text
    assert [r["id"] for r in res.json()] == [str(mine_run)]

    outsider = await client.get(f"/api/review-runs?workspace={mine}", headers=_as("nobody"))
    assert outsider.status_code == 404
    assert outsider.json()["error"]["code"] == "workspace_not_found"
