"""A2A の入口（S59）: state が無くても app を組み立てられる（本番は state を渡さない）。"""

from __future__ import annotations

import os
import subprocess
import sys

import httpx
import pytest

from speclino.api.app import API_PREFIXES, create_app
from speclino.settings import Settings


async def test_app_builds_without_state_and_card_is_public() -> None:
    app = create_app(Settings(env="local", role="all", code_host_kind="local", a2a_enabled=True))
    # lifespan を走らせない（app.state.speclino が無い）。Agent Card とトークン無しの 401 は
    # state を引かずに返る
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as http:
        card = await http.get("/.well-known/agent-card.json")
        assert card.status_code == 200 and card.json()["name"] == "SpecLino"
        assert card.json()["supportedInterfaces"][0]["url"].endswith("/a2a")
        assert (await http.post("/a2a", json={})).status_code == 401
    assert {"a2a", ".well-known"} <= set(API_PREFIXES)


def _loads_a2a(module: str) -> bool:
    """別プロセスで module を import し、a2a-sdk が読み込まれたかを返す（A2A は無効にして）。

    同じプロセスでは他のテストが a2a を読み込み済みなので、まっさらなプロセスで確かめる。
    """
    env = {**os.environ, "SPECLINO_A2A_ENABLED": "false"}
    code = f"import sys, {module}; print('a2a' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip().splitlines()[-1] == "True"


@pytest.mark.parametrize("module", ["speclino.api.app", "speclino.cli"])
def test_a2a_sdk_is_not_loaded_unless_needed(module: str) -> None:
    """worker / sandbox の起動や CLI の他のコマンドで a2a-sdk を読み込まない（S60）。"""
    assert not _loads_a2a(module)


def test_a2a_is_not_mounted_when_disabled() -> None:
    app = create_app(Settings(env="local", role="all", code_host_kind="local"))
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/a2a" not in paths and "/.well-known/agent-card.json" not in paths
