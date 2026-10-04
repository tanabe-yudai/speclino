"""A2A の入口（S59）: state が無くても app を組み立てられる（本番は state を渡さない）。"""

from __future__ import annotations

import httpx

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


def test_a2a_is_not_mounted_when_disabled() -> None:
    app = create_app(Settings(env="local", role="all", code_host_kind="local"))
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/a2a" not in paths and "/.well-known/agent-card.json" not in paths
