"""GCS の BlobStore（本番用、S26）。

`google-cloud-storage` は同期クライアントしか無いため、`asyncio.to_thread` で包んで BlobStore
ポート（非同期メソッド）に合わせる。`adapters/sandbox/docker_local.py` が同期の `subprocess.run`
を呼ぶのと同じ「ブロッキング I/O はスレッドに逃がす」考え方。
"""

from __future__ import annotations

import asyncio

import google.cloud.storage as storage
from google.api_core.exceptions import Forbidden, NotFound


class GcsBlobStore:
    def __init__(self, bucket: str, client: storage.Client | None = None) -> None:
        self.bucket = bucket
        self._client = client

    @property
    def client(self) -> storage.Client:
        """クライアントは遅延生成する。

        `storage.Client()` の生成自体が既定の認証情報（ADC）を要求するため、起動時
        （`api/deps.py` の `build_state`）に作ると ADC の無い環境で落ちる。`api/deps.py` の
        `LazyCodeHost` と同じ理由。テストでは `client` を注入して差し替える。
        """
        if self._client is None:
            self._client = storage.Client()
        return self._client

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        def _put() -> None:
            self.client.bucket(self.bucket).blob(key).upload_from_string(
                data, content_type=content_type
            )

        await asyncio.to_thread(_put)
        return f"gs://{self.bucket}/{key}"

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                data: bytes = self.client.bucket(self.bucket).blob(key).download_as_bytes()
            except NotFound as e:
                # fs.py（FileSystemBlobStore）と同じ例外にそろえる。
                # api/routes/review_runs.py の get_artifact の 404 がこれに依存する。
                raise FileNotFoundError(key) from e
            except Forbidden as e:
                # 権限の設定ミスを「成果物が無い」と取り違えない（呼び出し側で 503 にする）
                raise PermissionError(key) from e
            return data

        return await asyncio.to_thread(_get)

    async def list(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            blobs = self.client.list_blobs(self.bucket, prefix=prefix)
            return sorted(b.name for b in blobs)

        return await asyncio.to_thread(_list)


__all__ = ["GcsBlobStore"]
