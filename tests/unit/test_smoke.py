"""パッケージの基本的な動作を確認するスモークテスト。"""

from __future__ import annotations

import json

import httpx
import pytest

import speclino
from speclino.api.app import create_app
from speclino.settings import Settings


def test_import_and_version() -> None:
    assert speclino.__version__ == "0.1.0"


@pytest.mark.asyncio
async def test_health() -> None:
    app = create_app(Settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "all"}


def test_committed_openapi_is_fresh() -> None:
    """openapi.json（API-ERR-001 の入力）はルート変更のたびに `make openapi` で再生成する。"""
    from pathlib import Path

    generated = create_app(Settings(env="local", role="all")).openapi()
    committed = json.loads(
        (Path(__file__).resolve().parents[2] / "openapi.json").read_text(encoding="utf-8")
    )
    assert committed == generated, "openapi.json が古い。`make openapi` で再生成する"


@pytest.mark.asyncio
async def test_error_responses_use_error_response_shape() -> None:
    """HTTPException も入力検証エラー（422）も {"error": {"code", "message"}} で返す。"""
    app = create_app(Settings(env="local", role="all", code_host_kind="local"))
    transport = httpx.ASGITransport(app=app)
    # 依存関係（get_state）が lifespan で作る state を参照するため、lifespan 内で叩く
    # （DB へは接続しない: 入力検証で 422 になりハンドラまで到達しない）
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        validation = await client.post("/internal/tasks/run", json={"run_id": "not-a-uuid"})
        bad_path = await client.get("/api/review-runs/not-a-uuid/artifacts/x.json")
        unknown_route = await client.get("/api/no-such-route")
        wrong_method = await client.delete("/health")
    assert validation.status_code == 422
    assert validation.json()["error"]["code"] == "validation_error"
    assert bad_path.status_code == 422 and "error" in bad_path.json()
    # ルーティング由来（Starlette）の 404 / 405 も同じ形式
    assert (
        unknown_route.status_code == 404 and unknown_route.json()["error"]["code"] == "http_error"
    )
    assert wrong_method.status_code == 405 and "error" in wrong_method.json()


@pytest.mark.asyncio
async def test_spa_deep_links_fall_back_to_index() -> None:
    """/runs/<id> のようなクライアント側ルートは index.html を返す（API の 404 はそのまま）。"""
    from pathlib import Path

    if not (Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html").is_file():
        pytest.skip("web/dist が無い（make web-build）")
    app = create_app(Settings(env="local", role="all", code_host_kind="local"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        deep = await c.get("/runs/00000000-0000-0000-0000-000000000000")
        api = await c.get("/api/no-such-route")
    assert deep.status_code == 200 and '<div id="root"' in deep.text
    assert api.status_code == 404 and api.json()["error"]["code"] == "http_error"


def test_web_role_starts_without_github_credentials() -> None:
    """秘密鍵は worker だけが持つ（design のサービス構成表）。web は遅延生成で起動できる。"""
    from speclino.api.deps import LazyCodeHost, build_state

    state = build_state(Settings(env="prod", role="web", code_host_kind="github"))
    assert isinstance(state.code_host, LazyCodeHost)
    # 実際に GitHub を使う操作で初めて設定不足が分かる（= worker 側で失敗する）
    with pytest.raises(RuntimeError, match="GitHub App"):
        _ = state.code_host.inner
