"""web → worker のユーザー ID 問い合わせの委譲クライアント（S61）。"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from speclino.adapters.worker.github_user_client import USER_ID_PATH, WorkerGitHubUserLookup
from speclino.ports.github_users import GitHubUserLookupError


def _lookup(
    handle: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
    seen: list[httpx.Request] | None = None,
) -> WorkerGitHubUserLookup:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handle(request)

    lookup = WorkerGitHubUserLookup(
        "https://worker.example/", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(lookup, "_id_token", lambda: "id-token")
    return lookup


async def test_posts_the_login_with_an_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    lookup = _lookup(lambda r: httpx.Response(200, json={"id": 583231}), monkeypatch, seen)

    assert await lookup.user_id("octo-cat") == 583231
    [req] = seen
    assert str(req.url) == f"https://worker.example{USER_ID_PATH}"
    assert req.headers["authorization"] == "Bearer id-token"
    assert json.loads(req.content) == {"login": "octo-cat"}


async def test_unknown_login_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = _lookup(lambda r: httpx.Response(200, json={"id": None}), monkeypatch)
    assert await lookup.user_id("ghost-user") is None


async def test_a_503_carries_the_workers_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": {"code": "github_lookup_failed", "message": "GitHub の応答が 403"}}
    lookup = _lookup(lambda r: httpx.Response(503, json=body), monkeypatch)
    with pytest.raises(GitHubUserLookupError, match="GitHub の応答が 403"):
        await lookup.user_id("octo-cat")


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_other_worker_errors_name_the_status(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = _lookup(lambda r: httpx.Response(status, text="denied"), monkeypatch)
    with pytest.raises(GitHubUserLookupError, match=str(status)):
        await lookup.user_id("octo-cat")


async def test_transport_failure_is_a_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(GitHubUserLookupError, match="ConnectError"):
        await _lookup(fail, monkeypatch).user_id("octo-cat")


async def test_a_malformed_answer_is_a_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = _lookup(lambda r: httpx.Response(200, json={"id": "1"}), monkeypatch)
    with pytest.raises(GitHubUserLookupError):
        await lookup.user_id("octo-cat")


async def test_rejects_a_login_that_is_not_a_github_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []
    lookup = _lookup(lambda r: httpx.Response(200, json={"id": 1}), monkeypatch, seen)
    with pytest.raises(ValueError):
        await lookup.user_id("../orgs/x")
    assert seen == []
