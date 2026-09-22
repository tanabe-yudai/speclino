"""MCP サーバー（S33 / 暫定仕様書 FR「MCP 操作」）。

エディタのエージェント（Claude Code 等）が、仕様を書くための語彙を取り出し（`context_get`）、
検証を開始し（`verification_start`）、結果を読む（`verification_get`）ためのツール。判定を
書き換えるツールは無い（判定は engine だけが行う）。

- 認証は静的 Bearer（`SPECLINO_MCP_TOKEN`）。未設定ならサーバーをマウントしない
- Streamable HTTP を FastAPI に `/mcp` としてマウントする（`mount_mcp`）。
  stateless / JSON 応答にして Cloud Run の複数インスタンスでも動くようにする
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from speclino.api.deps import AppState
from speclino.db.repositories import FindingRepository, ReviewRunRepository
from speclino.spec import load_spec_ir

StateGetter = Callable[[], AppState]
MCP_PATH = "/mcp"
FINDING_FIELDS = ("requirement_id", "verdict", "category", "severity", "message", "next_steps")


def build_mcp(get_state: StateGetter) -> MCPServer:
    mcp = MCPServer(
        "speclino",
        instructions=(
            "SpecLino: 仕様（Spec IR）とコードの意味差分を Z3 で検査する。context_get で語彙と"
            "既存の要求を読み、verification_start で検証を開始し、verification_get で結果を読む。"
            "判定（verdict）は変更できない。"
        ),
    )

    @mcp.tool()
    async def context_get(repo_full_name: str, ref: str = "HEAD") -> dict[str, Any]:
        """リポジトリの `.speclino/`（ドメイン語彙・束縛・規約）と `specs/ir` の要求一覧を返す。

        仕様を書くときは、ここに出る変数・列挙・操作 ID だけを使う。
        """
        state = get_state()
        with tempfile.TemporaryDirectory(prefix="speclino-mcp-") as tmp:
            root = await state.code_host.fetch_snapshot(repo_full_name, ref, Path(tmp))
            return _read_context(root)

    @mcp.tool()
    async def verification_start(
        repo_full_name: str,
        head_sha: str,
        base_sha: str | None = None,
        pr_number: int | None = None,
    ) -> dict[str, str]:
        """head_sha の検証を開始し run_id を返す。結果は verification_get で読む。"""
        state = get_state()
        run_id = await state.service.create_run(
            repo_full_name=repo_full_name,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_number=pr_number,
            trigger="mcp",
        )
        return {"run_id": str(run_id)}

    @mcp.tool()
    async def verification_get(run_id: str) -> dict[str, Any]:
        """run の状態・ゲート結果・Finding（要求 ID / 判定 / 分類 / 説明 / 次にできること）。"""
        state = get_state()
        rid = uuid.UUID(run_id)
        async with state.session_factory() as session:
            run = await ReviewRunRepository(session).get(rid)
            if run is None:
                return {"error": "run_not_found", "run_id": run_id}
            findings = await FindingRepository(session).list_for_run(rid)
        manifest = run.manifest or {}
        return {
            "run_id": run_id,
            "exec_state": run.exec_state,
            "gate_status": run.gate_status,
            "gate": manifest.get("gate"),
            "semantic_diff": manifest.get("semantic_diff"),
            "findings": [
                {k: f.payload.get(k) for k in FINDING_FIELDS if k in f.payload} for f in findings
            ],
        }

    return mcp


def _read_context(root: Path) -> dict[str, Any]:
    speclino_dir = root / ".speclino"
    out: dict[str, Any] = {"domain": None, "bindings": None, "policies": [], "rules": []}
    for name in ("domain", "bindings"):
        path = speclino_dir / f"{name}.yaml"
        if path.is_file():
            out[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy_dir = speclino_dir / "policy"
    if policy_dir.is_dir():
        for path in sorted(policy_dir.glob("*.y*ml")):
            package = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            out["policies"].extend(
                {"id": r.get("id"), "level": r.get("level"), "title": r.get("title")}
                for r in package.get("rules", [])
            )
    ir_dir = root / "specs" / "ir"
    if ir_dir.is_dir():
        for path in sorted(ir_dir.glob("*.spec-ir.json")):
            spec = load_spec_ir(path)
            out["rules"].extend(
                {
                    "id": r.id,
                    "behavior_id": r.behavior_id,
                    "title": r.title,
                    "level": r.level,
                    "modality": r.modality,
                    "approved": r.approval_ref is not None,
                    "spec": next((s.path for s in r.source_refs if s.path), None),
                }
                for r in spec.rules
            )
    return out


class BearerAuth:
    """静的 Bearer トークンで MCP エンドポイントを守る ASGI ミドルウェア。"""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self.token}":
                response = JSONResponse(
                    {"error": {"code": "unauthenticated", "message": "Bearer トークンが必要"}},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def allowed_hosts(public_url: str) -> list[str]:
    """DNS rebinding 対策の Host 許可リスト（公開 URL のホストとローカル）。"""
    host = urlparse(public_url).hostname or "localhost"
    return sorted({host, f"{host}:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"})


def mount_mcp(
    get_state: StateGetter, token: str, public_url: str
) -> tuple[ASGIApp, Callable[[], AbstractAsyncContextManager[None]]]:
    """`/mcp` にマウントする ASGI アプリと、lifespan で入る session manager を返す。"""
    mcp = build_mcp(get_state)
    security = TransportSecuritySettings(allowed_hosts=allowed_hosts(public_url))
    # Mount ではなく `/mcp` の完全一致ルートとして登録するため、内部パスも `/mcp` にする
    # （Mount だと `/mcp/` にしか応答せず、`/` の静的配信に `/mcp` を取られる）
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
    return BearerAuth(app, token), mcp.session_manager.run


__all__ = ["MCP_PATH", "BearerAuth", "allowed_hosts", "build_mcp", "mount_mcp"]
