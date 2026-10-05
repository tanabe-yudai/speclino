"""app_auth の単体試験（S19）。JWT クレームと installation token のキャッシュを確認する。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from speclino.adapters.github.app_auth import GitHubAppAuth

BASE_URL = "https://api.github.com"


def _generate_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


PRIVATE_KEY_PEM = _generate_private_key_pem()


# ---------------------------------------------------------------- app_jwt


def test_app_jwt_claims_match_github_requirements() -> None:
    auth = GitHubAppAuth(app_id=12345, private_key_pem=PRIVATE_KEY_PEM)
    now = 1_700_000_000
    token = auth.app_jwt(now=now)

    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "12345"
    assert claims["iat"] == now - 60
    assert claims["exp"] == now + 540
    assert claims["exp"] - claims["iat"] <= 600  # GitHub の上限は 10 分


def test_app_jwt_header_uses_rs256() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    token = auth.app_jwt(now=1_700_000_000)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"


# ---------------------------------------------------------------- installation_token


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_fetches_from_api() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    route = respx.post(f"{BASE_URL}/app/installations/99/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_abc123", "expires_at": "2099-01-01T00:00:00Z"},
        )
    )

    async with httpx.AsyncClient() as http:
        token = await auth.installation_token(99, http, BASE_URL)

    assert token == "ghs_abc123"
    assert route.call_count == 1
    sent_request = route.calls.last.request
    assert sent_request.headers["accept"] == "application/vnd.github+json"
    assert sent_request.headers["authorization"].startswith("Bearer ")


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_is_cached_until_near_expiry() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    route = respx.post(f"{BASE_URL}/app/installations/99/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_first", "expires_at": "2099-01-01T00:00:00Z"},
        )
    )

    async with httpx.AsyncClient() as http:
        token1 = await auth.installation_token(99, http, BASE_URL)
        token2 = await auth.installation_token(99, http, BASE_URL)

    assert token1 == token2 == "ghs_first"
    assert route.call_count == 1  # 2 回目は HTTP を呼ばない


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_refetches_when_near_expiry() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    # 5分マージン内（3分後）に切れるので、2回目の呼出しでも再取得する
    near_expiry = (datetime.now(UTC) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    route = respx.post(f"{BASE_URL}/app/installations/99/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_soon", "expires_at": near_expiry},
        )
    )

    async with httpx.AsyncClient() as http:
        await auth.installation_token(99, http, BASE_URL)
        await auth.installation_token(99, http, BASE_URL)

    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_cache_is_per_installation() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    respx.post(f"{BASE_URL}/app/installations/1/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_1", "expires_at": "2099-01-01T00:00:00Z"}
        )
    )
    respx.post(f"{BASE_URL}/app/installations/2/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_2", "expires_at": "2099-01-01T00:00:00Z"}
        )
    )

    async with httpx.AsyncClient() as http:
        token1 = await auth.installation_token(1, http, BASE_URL)
        token2 = await auth.installation_token(2, http, BASE_URL)

    assert token1 == "ghs_1"
    assert token2 == "ghs_2"


# ---------------------------------------------------------------- any_installation_id


@respx.mock
@pytest.mark.asyncio
async def test_any_installation_id_takes_one_of_the_apps_installations_once() -> None:
    """S61: リポジトリに紐付かない問い合わせ（ユーザー ID）に使うインストール。"""
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    route = respx.get(f"{BASE_URL}/app/installations").mock(
        return_value=httpx.Response(200, json=[{"id": 31}, {"id": 32}])
    )

    async with httpx.AsyncClient() as http:
        first = await auth.any_installation_id(http, BASE_URL)
        second = await auth.any_installation_id(http, BASE_URL)

    assert first == second == 31
    assert route.call_count == 1
    assert route.calls.last.request.headers["authorization"].startswith("Bearer ")


@respx.mock
@pytest.mark.asyncio
async def test_any_installation_id_without_installations_is_lookup_error() -> None:
    auth = GitHubAppAuth(app_id=1, private_key_pem=PRIVATE_KEY_PEM)
    respx.get(f"{BASE_URL}/app/installations").mock(return_value=httpx.Response(200, json=[]))

    async with httpx.AsyncClient() as http:
        with pytest.raises(LookupError):
            await auth.any_installation_id(http, BASE_URL)
