"""パッケージの基本的な動作を確認するスモークテスト。"""

from __future__ import annotations

import httpx
import pytest

import speclino
from speclino.api.app import create_app
from speclino.settings import Settings


def test_import_and_version() -> None:
    assert speclino.__version__ == "0.1.0"


@pytest.mark.asyncio
async def test_healthz() -> None:
    app = create_app(Settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "all"}
