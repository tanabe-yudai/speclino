"""GcsBlobStore の単体試験（S26）。実際の GCS は呼ばず、フェイクのクライアントで検証する。"""

from __future__ import annotations

import pytest
from google.api_core.exceptions import Forbidden, NotFound

from speclino.adapters.storage.gcs import GcsBlobStore

BUCKET = "speclino-artifacts-test"


class _FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self.name = name

    def upload_from_string(self, data: bytes, content_type: str = "text/plain") -> None:
        self._store[self.name] = data

    def download_as_bytes(self) -> bytes:
        if self.name.startswith("forbidden/"):
            raise Forbidden(f"{self.name}: storage.objects.get denied")
        if self.name not in self._store:
            raise NotFound(f"{self.name} not found")
        return self._store[self.name]


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._store, name)


class _FakeClient:
    """storage.Client の代わりに使う。bucket 名を問わず単一のストアに読み書きする。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._store)

    def list_blobs(self, bucket_or_name: str, prefix: str = "") -> list[_FakeBlob]:
        return [_FakeBlob(self._store, name) for name in self._store if name.startswith(prefix)]


def _store(client: _FakeClient) -> GcsBlobStore:
    return GcsBlobStore(BUCKET, client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    client = _FakeClient()

    uri = await _store(client).put("runs/1/review.md", b"hello", content_type="text/markdown")

    assert uri == f"gs://{BUCKET}/runs/1/review.md"
    assert await _store(client).get("runs/1/review.md") == b"hello"


@pytest.mark.asyncio
async def test_list_returns_sorted_keys_matching_prefix() -> None:
    client = _FakeClient()
    store = _store(client)
    await store.put("runs/1/b.json", b"{}")
    await store.put("runs/1/a.json", b"{}")
    await store.put("runs/2/c.json", b"{}")

    keys = await store.list("runs/1/")

    assert keys == ["runs/1/a.json", "runs/1/b.json"]


@pytest.mark.asyncio
async def test_get_missing_key_raises_file_not_found_error() -> None:
    """fs.py（FileSystemBlobStore）と同じ例外にそろえる。api の 404 がこれに依存する。"""
    client = _FakeClient()

    with pytest.raises(FileNotFoundError):
        await _store(client).get("runs/none/missing.json")


def test_client_is_created_lazily() -> None:
    """コンストラクタでは storage.Client を作らない（ADC の無い環境でも構築できる）。"""
    store = GcsBlobStore(BUCKET)

    assert store._client is None


async def test_get_without_permission_raises_permission_error() -> None:
    """読む権限が無い（403）ときは「無い」とは区別する。API は 404 ではなく 503 にする。"""
    store = _store(_FakeClient())
    with pytest.raises(PermissionError):
        await store.get("forbidden/runs/x/review.md")
