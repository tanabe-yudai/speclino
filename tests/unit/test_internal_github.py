"""worker の内部エンドポイント `POST /internal/github/user-id`（S61）。

web は GitHub App の秘密鍵を持たないので、承認者のログイン名 → ユーザー ID を worker に任せる。
GitHub の代わりに偽のルックアップを state に差し込む（DB には触れない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import httpx

from speclino.api.app import create_app
from speclino.api.deps import build_state
from speclino.ports.github_users import GitHubUserLookupError
from speclino.settings import Settings


@dataclass
class _FakeUsers:
    ids: dict[str, int]
    error: str | None = None
    seen: list[str] = field(default_factory=list)

    async def user_id(self, login: str) -> int | None:
        if not login.isalnum():
            raise ValueError(f"GitHub のログイン名ではない: {login!r}")
        self.seen.append(login)
        if self.error is not None:
            raise GitHubUserLookupError(self.error)
        return self.ids.get(login)


async def _post(
    users: _FakeUsers, body: object, role: Literal["web", "worker"] = "worker"
) -> httpx.Response:
    settings = Settings(role=role)
    state = build_state(settings)
    state.github_users = users
    app = create_app(settings, state)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w") as http,
    ):
        return await http.post("/internal/github/user-id", json=body)


async def test_returns_the_user_id() -> None:
    users = _FakeUsers({"octocat": 583231})
    res = await _post(users, {"login": "octocat"})
    assert res.status_code == 200, res.text
    assert res.json() == {"id": 583231}
    assert users.seen == ["octocat"]


async def test_unknown_login_is_null() -> None:
    res = await _post(_FakeUsers({}), {"login": "ghost"})
    assert res.status_code == 200
    assert res.json() == {"id": None}


async def test_lookup_failure_is_503_with_the_upstream_reason() -> None:
    res = await _post(_FakeUsers({}, error="GitHub の応答が 429"), {"login": "octocat"})
    assert res.status_code == 503
    error = res.json()["error"]
    assert error["code"] == "github_lookup_failed"
    assert "429" in error["message"]


async def test_a_login_that_is_not_a_github_login_is_422() -> None:
    res = await _post(_FakeUsers({}), {"login": "../orgs/x"})
    assert res.status_code == 422


async def test_the_web_role_does_not_serve_it() -> None:
    res = await _post(_FakeUsers({"octocat": 1}), {"login": "octocat"}, role="web")
    assert res.status_code == 404
