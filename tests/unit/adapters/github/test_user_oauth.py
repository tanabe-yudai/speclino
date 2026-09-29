"""GitHub App のユーザー認可アダプター（S55）。GitHub は httpx.MockTransport で再現する。"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from speclino.adapters.github.user_oauth import (
    GitHubOAuthError,
    GitHubUserOAuth,
    authorize_url,
)


def _oauth(handler) -> GitHubUserOAuth:  # type: ignore[no-untyped-def]
    return GitHubUserOAuth("cid", "csecret", transport=httpx.MockTransport(handler))


def test_the_authorize_url_carries_state_and_an_s256_challenge() -> None:
    url = authorize_url(
        client_id="cid",
        redirect_uri="https://w/github/callback",
        state="st",
        code_verifier="v" * 43,
    )
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    expected = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).rstrip(b"=").decode()
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert q == {
        "client_id": "cid",
        "redirect_uri": "https://w/github/callback",
        "state": "st",
        "code_challenge": expected,
        "code_challenge_method": "S256",
    }


async def test_exchange_asks_for_json_and_sends_the_same_redirect_uri_and_verifier() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "ghu_x", "refresh_token": "ghr_y"})

    token = await _oauth(handle).exchange(
        code="c", redirect_uri="https://w/cb", code_verifier="ver"
    )
    assert token == "ghu_x"
    assert seen[0].headers["accept"] == "application/json"
    form = parse_qs(seen[0].content.decode())
    assert form["redirect_uri"] == ["https://w/cb"] and form["code_verifier"] == ["ver"]


@pytest.mark.parametrize(
    "error", ["bad_verification_code", "redirect_uri_mismatch", "access_denied"]
)
async def test_exchange_errors_name_only_the_reason(error: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": error, "error_description": "x"})

    with pytest.raises(GitHubOAuthError, match=error):
        await _oauth(handle).exchange(code="c", redirect_uri="r", code_verifier="v")


async def test_find_repository_walks_installations_and_reads_the_users_admin_permission() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghu_x"
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 11}, {"id": 22}]})
        if request.url.path == "/user/installations/11/repositories":
            return httpx.Response(200, json={"repositories": [{"id": 1, "full_name": "a/other"}]})
        repos = [
            {
                "id": 7,
                "full_name": "acme/widgets",
                "default_branch": "trunk",
                "permissions": {"admin": True},
            }
        ]
        return httpx.Response(200, json={"repositories": repos})

    repo = await _oauth(handle).find_repository("ghu_x", "Acme/Widgets")
    assert repo is not None
    assert (repo.github_repo_id, repo.installation_id, repo.default_branch, repo.admin) == (
        7,
        22,
        "trunk",
        True,
    )


async def test_a_repository_outside_the_installations_is_not_found() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": []})
        raise AssertionError(request.url)

    assert await _oauth(handle).find_repository("ghu_x", "acme/widgets") is None


async def test_revoke_deletes_the_grant_with_basic_auth_and_the_token_in_the_body() -> None:
    """grant ごと取り消す（access token と refresh token がまとめて失効する）。"""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    await _oauth(handle).revoke("ghu_x")
    assert seen[0].method == "DELETE" and seen[0].url.path == "/applications/cid/grant"
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()
    assert json.loads(seen[0].content) == {"access_token": "ghu_x"}


@pytest.mark.parametrize("status", [404, 422, 500])
async def test_anything_but_204_is_a_failed_revocation(status: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(GitHubOAuthError, match="取り消せない"):
        await _oauth(handle).revoke("ghu_x")
