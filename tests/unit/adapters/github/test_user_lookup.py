"""GitHub のログイン名 → ユーザー ID（S61）。GitHub は httpx.MockTransport で再現する。"""

from __future__ import annotations

import httpx
import pytest

from speclino.adapters.github.user_lookup import PublicGitHubUserLookup
from speclino.ports.github_users import GitHubUserLookupError


def _lookup(status: int, body: object = None, seen: list[httpx.Request] | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return PublicGitHubUserLookup(transport=httpx.MockTransport(handler))


async def test_returns_the_public_user_id_without_authentication() -> None:
    seen: list[httpx.Request] = []
    lookup = _lookup(200, {"login": "octo-cat", "id": 583231}, seen)
    assert await lookup.user_id("octo-cat") == 583231
    [req] = seen
    assert str(req.url) == "https://api.github.com/users/octo-cat"
    assert "authorization" not in req.headers


async def test_unknown_login_is_none() -> None:
    assert await _lookup(404, {"message": "Not Found"}).user_id("ghost-user") is None


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_rate_limit_and_server_errors_raise(status: int) -> None:
    with pytest.raises(GitHubUserLookupError):
        await _lookup(status, {"message": "API rate limit exceeded"}).user_id("octo-cat")


async def test_network_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    lookup = PublicGitHubUserLookup(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubUserLookupError):
        await lookup.user_id("octo-cat")


async def test_malformed_body_raises() -> None:
    with pytest.raises(GitHubUserLookupError):
        await _lookup(200, {"login": "octo-cat"}).user_id("octo-cat")


@pytest.mark.parametrize("login", ["../repos/o/r", "a/b", "", "a?x=1"])
async def test_rejects_non_login_values_without_calling_github(login: str) -> None:
    """ログイン名は URL のパスに埋め込むので、形の違う値で別のパスに移れないようにする。"""
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError):
        await _lookup(200, {"id": 1}, seen).user_id(login)
    assert seen == []
