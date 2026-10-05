"""GitHubClient.user_id（S61）: GitHub App の installation token で `GET /users/{login}` を引く。

認証なしの公開 API は Cloud Run の共有 IP でレート制限（60 回/時）に当たるため、worker は App の
認証で引く。認証は StubAuth に差し替え、HTTP は respx でモックする。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
import respx

from speclino.adapters.github.client import GitHubClient
from speclino.ports.github_users import GitHubUserLookupError

BASE_URL = "https://api.github.com"


@dataclass
class _StubAuth:
    installations: list[int] = field(default_factory=lambda: [77])
    token_status: int = 200
    used: list[int] = field(default_factory=list)

    async def any_installation_id(self, http: httpx.AsyncClient, base_url: str) -> int:
        if not self.installations:
            raise LookupError("GitHub App のインストールが無い")
        return self.installations[0]

    async def installation_token(
        self, installation_id: int, http: httpx.AsyncClient, base_url: str
    ) -> str:
        self.used.append(installation_id)
        if self.token_status != 200:
            request = httpx.Request("POST", f"{base_url}/app/installations/x/access_tokens")
            response = httpx.Response(self.token_status, request=request)
            raise httpx.HTTPStatusError("denied", request=request, response=response)
        return "inst-token"


def _client(auth: _StubAuth, installation_id: int = 0) -> GitHubClient:
    return GitHubClient(
        auth=auth,  # type: ignore[arg-type]
        installation_id=installation_id,
        http=httpx.AsyncClient(),
        base_url=BASE_URL,
    )


@respx.mock
async def test_user_id_uses_an_installation_token_of_the_app() -> None:
    auth = _StubAuth()
    route = respx.get(f"{BASE_URL}/users/octo-cat").mock(
        return_value=httpx.Response(200, json={"login": "octo-cat", "id": 583231})
    )

    assert await _client(auth).user_id("octo-cat") == 583231
    assert route.calls.last.request.headers["authorization"] == "Bearer inst-token"
    assert auth.used == [77]


@respx.mock
async def test_user_id_prefers_the_clients_installation() -> None:
    auth = _StubAuth()
    respx.get(f"{BASE_URL}/users/octo-cat").mock(return_value=httpx.Response(200, json={"id": 1}))

    await _client(auth, installation_id=42).user_id("octo-cat")
    assert auth.used == [42]


@respx.mock
async def test_user_id_of_an_unknown_login_is_none() -> None:
    respx.get(f"{BASE_URL}/users/ghost").mock(return_value=httpx.Response(404))
    assert await _client(_StubAuth()).user_id("ghost") is None


@pytest.mark.parametrize("status", [403, 429, 500])
@respx.mock
async def test_user_id_failure_names_the_status(status: int) -> None:
    respx.get(f"{BASE_URL}/users/octo-cat").mock(return_value=httpx.Response(status))
    with pytest.raises(GitHubUserLookupError, match=str(status)):
        await _client(_StubAuth()).user_id("octo-cat")


async def test_user_id_auth_failure_names_the_status() -> None:
    with pytest.raises(GitHubUserLookupError, match="401"):
        await _client(_StubAuth(token_status=401)).user_id("octo-cat")


async def test_user_id_without_an_installation_is_a_lookup_error() -> None:
    with pytest.raises(GitHubUserLookupError, match="インストール"):
        await _client(_StubAuth(installations=[])).user_id("octo-cat")


async def test_user_id_rejects_a_login_that_is_not_a_github_login() -> None:
    auth = _StubAuth()
    with pytest.raises(ValueError):
        await _client(auth).user_id("../orgs/x")
    assert auth.used == []
