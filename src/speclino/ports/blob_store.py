"""BlobStore ポート: 成果物（run-manifest / findings / review.md 等）の保存。"""

from __future__ import annotations

from typing import Protocol


class BlobStore(Protocol):
    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """保存して URI（gs://... または file://...）を返す。"""
        ...

    async def get(self, key: str) -> bytes: ...

    async def list(self, prefix: str) -> list[str]: ...


__all__ = ["BlobStore"]
