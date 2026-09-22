"""GitHubClient の単体試験（S19）。認証は StubAuth に差し替え、HTTP は respx でモックする。"""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import respx

from speclino.adapters.github.client import GitHubClient

BASE_URL = "https://api.github.com"


@dataclass
class _StubAuth:
    """GitHubAppAuth の代わりに使う。installation_token の実装だけを満たす。"""

    token: str = "test-installation-token"

    async def installation_token(
        self, installation_id: int, http: httpx.AsyncClient, base_url: str
    ) -> str:
        return self.token


def _client() -> GitHubClient:
    return GitHubClient(
        auth=_StubAuth(),  # type: ignore[arg-type]
        installation_id=42,
        http=httpx.AsyncClient(),
        base_url=BASE_URL,
    )


# ---------------------------------------------------------------- create_check


@respx.mock
@pytest.mark.asyncio
async def test_create_check_sends_expected_body() -> None:
    client = _client()
    route = respx.post(f"{BASE_URL}/repos/acme/widgets/check-runs").mock(
        return_value=httpx.Response(201, json={"id": 999})
    )

    check_run_id = await client.create_check(
        "acme/widgets",
        head_sha="sha-1",
        name="speclino",
        details_url="https://speclino.example/run/1",
        external_id="run-1",
    )

    assert check_run_id == 999
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer test-installation-token"
    payload = json.loads(sent.content)
    assert payload == {
        "name": "speclino",
        "head_sha": "sha-1",
        "details_url": "https://speclino.example/run/1",
        "external_id": "run-1",
    }


@respx.mock
@pytest.mark.asyncio
async def test_create_check_omits_optional_none_fields() -> None:
    client = _client()
    respx.post(f"{BASE_URL}/repos/acme/widgets/check-runs").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    await client.create_check("acme/widgets", "sha-1", "speclino", None, None)
    sent = respx.calls.last.request
    payload = json.loads(sent.content)
    assert "details_url" not in payload
    assert "external_id" not in payload


# ---------------------------------------------------------------- update_check


@respx.mock
@pytest.mark.asyncio
async def test_update_check_splits_120_annotations_into_3_patch_requests() -> None:
    client = _client()
    route = respx.patch(f"{BASE_URL}/repos/acme/widgets/check-runs/555").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    annotations = [
        {
            "path": "app.py",
            "start_line": i,
            "end_line": i,
            "annotation_level": "warning",
            "message": f"msg {i}",
            "title": f"title {i}",
        }
        for i in range(120)
    ]

    await client.update_check(
        "acme/widgets",
        555,
        status="completed",
        conclusion="failure",
        title="t",
        summary="s",
        text="body",
        annotations=annotations,
    )

    assert route.call_count == 3
    sizes = [len(json.loads(c.request.content)["output"]["annotations"]) for c in route.calls]
    assert sizes == [50, 50, 20]
    for call in route.calls:
        payload = json.loads(call.request.content)
        assert payload["status"] == "completed"
        assert payload["conclusion"] == "failure"
        assert payload["output"]["title"] == "t"


@respx.mock
@pytest.mark.asyncio
async def test_update_check_with_no_annotations_sends_single_request() -> None:
    client = _client()
    route = respx.patch(f"{BASE_URL}/repos/acme/widgets/check-runs/1").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    await client.update_check("acme/widgets", 1, "in_progress", None, "t", "s", "b", annotations=[])
    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert payload["output"]["annotations"] == []
    assert "conclusion" not in payload


# ---------------------------------------------------------------- post_comment


@respx.mock
@pytest.mark.asyncio
async def test_post_comment_returns_id() -> None:
    client = _client()
    route = respx.post(f"{BASE_URL}/repos/acme/widgets/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 321, "html_url": "..."})
    )
    comment_id = await client.post_comment("acme/widgets", 7, "hello")
    assert comment_id == 321
    assert json.loads(route.calls.last.request.content) == {"body": "hello"}


# ---------------------------------------------------------------- retry


@respx.mock
@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds() -> None:
    client = _client()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client._sleep = fake_sleep

    route = respx.post(f"{BASE_URL}/repos/acme/widgets/check-runs").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(201, json={"id": 42}),
        ]
    )

    check_run_id = await client.create_check("acme/widgets", "sha", "speclino", None, None)

    assert check_run_id == 42
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds() -> None:
    client = _client()

    async def fake_sleep(seconds: float) -> None:
        return None

    client._sleep = fake_sleep

    route = respx.post(f"{BASE_URL}/repos/acme/widgets/check-runs").mock(
        side_effect=[httpx.Response(429), httpx.Response(201, json={"id": 1})]
    )
    await client.create_check("acme/widgets", "sha", "speclino", None, None)
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_gives_up_after_max_retries_and_raises() -> None:
    client = _client()

    async def fake_sleep(seconds: float) -> None:
        return None

    client._sleep = fake_sleep

    route = respx.post(f"{BASE_URL}/repos/acme/widgets/check-runs").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_check("acme/widgets", "sha", "speclino", None, None)
    assert route.call_count == 4  # 初回 + 再試行3回


# ---------------------------------------------------------------- fetch_snapshot


def _build_tarball(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@respx.mock
@pytest.mark.asyncio
async def test_fetch_snapshot_follows_redirect_and_extracts(tmp_path: Path) -> None:
    client = _client()
    tar_bytes = _build_tarball(
        {
            "acme-widgets-abc1234/README.md": b"hello world",
            "acme-widgets-abc1234/src/app.py": b"print('hi')\n",
        }
    )
    respx.get(f"{BASE_URL}/repos/acme/widgets/tarball/abc1234").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://codeload.github.com/acme/widgets/tar.gz/abc1234"},
        )
    )
    respx.get("https://codeload.github.com/acme/widgets/tar.gz/abc1234").mock(
        return_value=httpx.Response(200, content=tar_bytes)
    )

    dest = tmp_path / "snapshot"
    result = await client.fetch_snapshot("acme/widgets", "abc1234", dest)

    assert result == dest / "acme-widgets-abc1234"
    assert (result / "README.md").read_text(encoding="utf-8") == "hello world"
    assert (result / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
