"""MCP サーバー（S33 / 暫定仕様書 FR「MCP 操作」）。

エディタのエージェント（Claude Code 等）が、仕様を書くための語彙とチーム規約を取り出し
（`context_get`）、検証を開始し（`verification_start`）、結果を読む（`verification_get`）ためのツール。判定を
書き換えるツールは無い（判定は engine だけが行う）。

- 認証はワークスペース単位のトークン（S54、`services/mcp_tokens.py`）。`BearerAuth` がトークンから
  ワークスペースを決めてリクエストに載せ、ツールはそのワークスペースに登録されたリポジトリと run
  にだけ答える（他のワークスペースのものは「無い」と同じ応答）
- `context_get` のスナップショット取得は GitHub App の秘密鍵が要るので、本番では worker に委譲する
  （`services/mcp_context.py`）
- Streamable HTTP を FastAPI に `/mcp` としてマウントする（`mount_mcp`）。
  stateless / JSON 応答にして Cloud Run の複数インスタンスでも動くようにする
- `instructions`（2KB 以内）と `speclino_guide` prompt（Claude Code 以外のクライアント向けに
  SKILL.md 本文を返す）は、どちらも `plugins/speclino/skills/speclino-spec-authoring/SKILL.md`
  （S57、Claude Code プラグインが配る Skill と同じファイル）から読む。二重に書かず正本を 1 つに保つ
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from speclino.api.deps import AppState
from speclino.db.models import Project
from speclino.db.repositories import FindingRepository, ProjectRepository, ReviewRunRepository
from speclino.domain.run import GitRef
from speclino.services import mcp_tokens
from speclino.services.mcp_context import ContextUnavailable, InProcessContextSource

StateGetter = Callable[[], AppState]
MCP_PATH = "/mcp"
FINDING_FIELDS = ("requirement_id", "verdict", "category", "severity", "message", "next_steps")
# BearerAuth がリクエストに載せる、トークンのワークスペース（slug）の置き場所
WORKSPACE_STATE_KEY = "mcp_workspace"

# instructions / prompt の正本（プラグインが配る Skill と同じファイル。S57）
SKILL_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "speclino"
    / "skills"
    / "speclino-spec-authoring"
    / "SKILL.md"
)
# front matter を落とした本文の中で、短い概要（instructions 用）を区切るマーカー
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)
_INSTRUCTIONS_RE = re.compile(
    r"<!-- mcp-instructions:start -->\n(.*?)\n<!-- mcp-instructions:end -->", re.DOTALL
)


def skill_body() -> str:
    """SKILL.md の本文（front matter を除いた全文）。`speclino_guide` prompt が返す値と同じ。"""
    raw = SKILL_PATH.read_text(encoding="utf-8")
    return _FRONT_MATTER_RE.sub("", raw, count=1).strip() + "\n"


def skill_instructions() -> str:
    """SKILL.md の `<!-- mcp-instructions -->` 区間から、MCP の instructions（2KB 以内）を作る。

    手で別に書くと SKILL.md とずれるので、同じファイルの同じ段落をそのまま使う。
    """
    m = _INSTRUCTIONS_RE.search(SKILL_PATH.read_text(encoding="utf-8"))
    if m is None:
        raise ValueError(f"{SKILL_PATH} に <!-- mcp-instructions --> マーカーが無い")
    return m.group(1).strip()


def build_mcp(get_state: StateGetter) -> MCPServer:
    mcp = MCPServer("speclino", instructions=skill_instructions())

    @mcp.prompt()
    def speclino_guide() -> str:
        """SpecLino の使い方（SKILL.md 本文）。Claude Code 以外の MCP クライアント向け。"""
        return skill_body()

    @mcp.tool()
    async def context_get(
        repo_full_name: str, ctx: Context, ref: GitRef = "HEAD"
    ) -> dict[str, Any]:
        """語彙（`.speclino/`）、`specs/ir` の要求一覧、このリポジトリに効く規約を返す。

        仕様を書くときは、ここに出る変数・列挙・操作 ID だけを使う。実装するときは `policies`
        （チーム規約 ＋ プロジェクト固有の追加規約）に従う。`verifier` が null の規約は
        機械検証されない（助言のみ）が、守る対象であることは変わらない。
        """
        state = get_state()
        workspace = _workspace(ctx)
        project = await _workspace_project(state, workspace, repo_full_name)
        source = state.context_source or InProcessContextSource(
            state.session_factory, state.code_host, state.service.config.resolve_team_policy
        )
        try:
            return await source.get(project.id, workspace, ref)
        except ContextUnavailable as e:
            raise ToolError(str(e)) from e

    @mcp.tool()
    async def verification_start(
        repo_full_name: str,
        head_sha: GitRef,
        ctx: Context,
        base_sha: GitRef | None = None,
        pr_number: int | None = None,
    ) -> dict[str, str]:
        """head_sha の検証を開始し run_id を返す。結果は verification_get で読む。"""
        state = get_state()
        project = await _workspace_project(state, _workspace(ctx), repo_full_name)
        run_id = await state.service.create_run(
            project_id=project.id,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_number=pr_number,
            trigger="mcp",
        )
        return {"run_id": str(run_id)}

    @mcp.tool()
    async def verification_get(run_id: str, ctx: Context) -> dict[str, Any]:
        """run の状態・ゲート結果・Finding（要求 ID / 判定 / 分類 / 説明 / 次にできること）。"""
        state = get_state()
        workspace = _workspace(ctx)
        rid = uuid.UUID(run_id)
        async with state.session_factory() as session:
            run = await ReviewRunRepository(session).get(rid)
            project = await session.get(Project, run.project_id) if run is not None else None
            # トークンのワークスペースの run でなければ、無い場合と同じ応答にする
            if run is None or project is None or project.team != workspace:
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


def _workspace(ctx: Context) -> str:
    """トークンのワークスペース。`BearerAuth` を通っていないリクエストは拒否する。"""
    request = ctx.request_context.request
    workspace = getattr(request.state, WORKSPACE_STATE_KEY, None) if request is not None else None
    if not isinstance(workspace, str):
        raise ToolError("MCP トークンが無い")
    return workspace


async def _workspace_project(state: AppState, workspace: str, repo_full_name: str) -> Project:
    """トークンのワークスペースに登録されたリポジトリの project。それ以外は拒否する。

    未登録・他のワークスペース・存在しないを区別しない（存在を漏らさない）。
    """
    async with state.session_factory() as session:
        project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if project is None or project.team != workspace:
        raise ToolError(f"{repo_full_name} はこのワークスペースに登録されていない")
    return project


class BearerAuth:
    """ワークスペース単位の MCP トークンで `/mcp` を守る ASGI ミドルウェア（S54）。

    有効なトークンなら、そのワークスペースを `scope["state"]` に載せて通す。無い・不正・失効済みは
    区別せず 401。
    """

    def __init__(self, app: ASGIApp, get_state: StateGetter) -> None:
        self.app = app
        self.get_state = get_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = Request(scope, receive).headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            workspace = await self._authenticate(token) if token else None
            if workspace is None:
                response = JSONResponse(
                    {"error": {"code": "unauthenticated", "message": "MCP トークンが必要"}},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})[WORKSPACE_STATE_KEY] = workspace
        await self.app(scope, receive, send)

    async def _authenticate(self, token: str) -> str | None:
        # 形式の違う値は authenticate が DB を引く前に拒否する
        # （セッションは最初の照会まで接続しない）
        async with self.get_state().session_factory() as session, session.begin():
            return await mcp_tokens.authenticate(session, token)


def allowed_hosts(public_url: str) -> list[str]:
    """DNS rebinding 対策の Host 許可リスト（公開 URL のホストとローカル）。"""
    host = urlparse(public_url).hostname or "localhost"
    return sorted({host, f"{host}:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"})


def mount_mcp(
    get_state: StateGetter, public_url: str
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
    return BearerAuth(app, get_state), mcp.session_manager.run


__all__ = [
    "MCP_PATH",
    "BearerAuth",
    "allowed_hosts",
    "build_mcp",
    "mount_mcp",
    "skill_body",
    "skill_instructions",
]
