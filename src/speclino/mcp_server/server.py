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
- S68: sync トークン（`slsync_`、`services/sync_tokens.py`）も受ける。sync トークンは読み取り専用で
  1 つのプロジェクトに限る（`verification_start` は拒否、他のプロジェクトは「無い」と同じ応答）。
  チームの知識を読む `sync_status` / `spec_get` / `skill_get` は承認済みの今の bundle から答え、
  取得を `knowledge_fetches` に残す
- `instructions`（2KB 以内）と `speclino_guide` prompt（Claude Code 以外のクライアント向けに
  SKILL.md 本文を返す）は、どちらも `plugins/speclino/skills/speclino-spec-authoring/SKILL.md`
  （S57、Claude Code プラグインが配る Skill と同じファイル）から読む。二重に書かず正本を 1 つに保つ
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
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
from speclino.db.repositories import ProjectRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.db.repositories.sync_tokens import KnowledgeFetchRepository
from speclino.domain.knowledge import (
    ArtifactRevision,
    KnowledgeLock,
    ResolvedKnowledge,
    SkillContent,
    SpecContent,
)
from speclino.domain.run import GitRef
from speclino.services import knowledge_registry, knowledge_sync, mcp_tokens, sync_tokens
from speclino.services.mcp_context import ContextUnavailable, InProcessContextSource
from speclino.services.run_view import run_view

StateGetter = Callable[[], AppState]
MCP_PATH = "/mcp"
# BearerAuth がリクエストに載せる、トークンのワークスペース（slug）の置き場所
WORKSPACE_STATE_KEY = "mcp_workspace"
# 同じく、トークンの身元（`Principal`）の置き場所
PRINCIPAL_STATE_KEY = "mcp_principal"


@dataclass(frozen=True)
class Principal:
    """MCP の呼び出し元。`sync_project` があれば sync トークン（読み取り専用）。"""

    workspace: str
    token_id: uuid.UUID
    sync_project: uuid.UUID | None = None

    @property
    def channel(self) -> str:
        return "sync" if self.sync_project is not None else "mcp"


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
        project = await _workspace_project(state, _principal(ctx), repo_full_name)
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
        principal = _principal(ctx)
        if principal.sync_project is not None:
            raise ToolError("sync トークンは読み取り専用で、検証を開始できない")
        project = await _workspace_project(state, principal, repo_full_name)
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
        principal = _principal(ctx)
        rid = uuid.UUID(run_id)
        if principal.sync_project is not None and not await _run_in_project(
            state, rid, principal.sync_project
        ):
            return {"error": "run_not_found", "run_id": run_id}
        view = await run_view(state.session_factory, rid, principal.workspace)
        # トークンのワークスペースの run でなければ、無い場合と同じ応答にする
        if view is None:
            return {"error": "run_not_found", "run_id": run_id}
        return {**view, "run_id": run_id}

    @mcp.tool()
    async def sync_status(repo_full_name: str, lock_json: str, ctx: Context) -> dict[str, Any]:
        """手元の `.speclino/lock.json`（本文）が今の承認済みの知識と比べて古いか。

        `stale` が true なら `speclino sync` で最新にする。`changed` は世代の間で変わった知識。
        """
        principal = _principal(ctx)
        _, resolved = await _resolve(get_state(), principal, repo_full_name)
        return _sync_status(resolved, lock_json)

    @mcp.tool()
    async def spec_get(repo_full_name: str, name: str, ctx: Context) -> dict[str, Any]:
        """このリポジトリに当たる、承認済みの仕様（Markdown）を名前で返す。"""
        state = get_state()
        principal = _principal(ctx)
        project, resolved = await _resolve(state, principal, repo_full_name)
        rev = _pick(resolved.specs, name)
        assert isinstance(rev.content, SpecContent)
        await _record_fetch(state, principal, project, resolved, rev)
        return {
            **_rev_view(resolved, rev),
            "markdown": rev.content.markdown,
        }

    @mcp.tool()
    async def skill_get(repo_full_name: str, name: str, ctx: Context) -> dict[str, Any]:
        """このリポジトリに当たる、承認済みのスキル（SKILL.md と付属ファイル）を名前で返す。"""
        state = get_state()
        principal = _principal(ctx)
        project, resolved = await _resolve(state, principal, repo_full_name)
        rev = _pick(resolved.skills, name)
        assert isinstance(rev.content, SkillContent)
        await _record_fetch(state, principal, project, resolved, rev)
        return {
            **_rev_view(resolved, rev),
            "skill_md": rev.content.skill_md,
            "files": dict(rev.content.files),
        }

    return mcp


def _rev_view(resolved: ResolvedKnowledge, rev: ArtifactRevision) -> dict[str, Any]:
    return {
        "name": rev.name,
        "revision": rev.revision,
        "content_hash": rev.content_hash,
        "bundle_generation": resolved.bundle.generation,
    }


def _pick(revs: list[ArtifactRevision], name: str) -> ArtifactRevision:
    for rev in revs:
        if rev.name == name:
            return rev
    raise ToolError(f"{name} はこのリポジトリに当たる承認済みの知識に無い")


async def _resolve(
    state: AppState, principal: Principal, repo_full_name: str
) -> tuple[Project, ResolvedKnowledge]:
    """リポジトリに当たる今の bundle（無ければ作る）の内容。"""
    project = await _workspace_project(state, principal, repo_full_name)
    try:
        async with state.session_factory() as session, session.begin():
            bundle = await knowledge_registry.ensure_bundle(session, principal.workspace)
            resolved = await knowledge_registry.resolve_for_project(
                session, bundle, str(project.id)
            )
    except LookupError as e:
        raise ToolError(f"{repo_full_name} はこのワークスペースに登録されていない") from e
    except ValueError as e:
        raise ToolError(f"知識を読めない: {e}") from e
    return project, resolved


async def _record_fetch(
    state: AppState,
    principal: Principal,
    project: Project,
    resolved: ResolvedKnowledge,
    rev: ArtifactRevision,
) -> None:
    async with state.session_factory() as session, session.begin():
        await KnowledgeFetchRepository(session).record(
            workspace=principal.workspace,
            channel=principal.channel,
            token_id=principal.token_id,
            bundle_generation=resolved.bundle.generation,
            project_id=project.id,
            artifact_ids=[rev.artifact_id],
        )


def _sync_status(resolved: ResolvedKnowledge, lock_json: str) -> dict[str, Any]:
    current = {
        m.artifact_id: m
        for m in resolved.bundle.members
        if m.artifact_id
        in {r.artifact_id for r in (*resolved.skills, *resolved.instructions, *resolved.specs)}
    }
    generation = resolved.bundle.generation
    try:
        lock = KnowledgeLock.model_validate_json(lock_json)
    except ValueError:
        return {
            "current_generation": generation,
            "lock_generation": None,
            "stale": True,
            "reason": "lock を読めない",
            "changed": [],
        }
    if not knowledge_sync.verify_lock(lock) or lock.workspace != resolved.bundle.workspace:
        return {
            "current_generation": generation,
            "lock_generation": lock.bundle_generation,
            "stale": True,
            "reason": "lock の署名が合わないか別のワークスペースのもの",
            "changed": [],
        }
    before = {m.artifact_id: m for m in lock.members}
    changed = []
    for aid in sorted(set(before) | set(current)):
        old, new = before.get(aid), current.get(aid)
        if old is not None and new is not None and old.content_hash == new.content_hash:
            continue
        ref = new or old
        assert ref is not None
        changed.append(
            {
                "name": ref.name,
                "kind": ref.kind,
                "lock_revision": old.revision if old else None,
                "current_revision": new.revision if new else None,
            }
        )
    return {
        "current_generation": generation,
        "lock_generation": lock.bundle_generation,
        "stale": bool(changed),
        "changed": sorted(changed, key=lambda c: (c["kind"], c["name"])),
    }


def _principal(ctx: Context) -> Principal:
    """トークンの身元。`BearerAuth` を通っていないリクエストは拒否する。"""
    request = ctx.request_context.request
    principal = getattr(request.state, PRINCIPAL_STATE_KEY, None) if request is not None else None
    if not isinstance(principal, Principal):
        raise ToolError("MCP トークンが無い")
    return principal


def _workspace(ctx: Context) -> str:
    """トークンのワークスペース。"""
    return _principal(ctx).workspace


async def _workspace_project(state: AppState, principal: Principal, repo_full_name: str) -> Project:
    """トークンのワークスペースに登録されたリポジトリの project。それ以外は拒否する。

    sync トークンはそのプロジェクトだけ。未登録・他のワークスペース・他のプロジェクト・存在しないを
    区別しない（存在を漏らさない）。
    """
    async with state.session_factory() as session:
        project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if (
        project is None
        or project.team != principal.workspace
        or (principal.sync_project is not None and project.id != principal.sync_project)
    ):
        raise ToolError(f"{repo_full_name} はこのワークスペースに登録されていない")
    return project


async def _run_in_project(state: AppState, run_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    async with state.session_factory() as session:
        run = await ReviewRunRepository(session).get(run_id)
    return run is not None and run.project_id == project_id


class BearerAuth:
    """MCP トークン（S54）か sync トークン（S68）で `/mcp` を守る ASGI ミドルウェア。

    有効なトークンなら、その身元とワークスペースを `scope["state"]` に載せて通す。無い・不正・
    失効済みは区別せず 401。
    """

    def __init__(self, app: ASGIApp, get_state: StateGetter) -> None:
        self.app = app
        self.get_state = get_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = Request(scope, receive).headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            principal = await self._authenticate(token) if token else None
            if principal is None:
                response = JSONResponse(
                    {"error": {"code": "unauthenticated", "message": "MCP トークンが必要"}},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
            state = scope.setdefault("state", {})
            state[WORKSPACE_STATE_KEY] = principal.workspace
            state[PRINCIPAL_STATE_KEY] = principal
        await self.app(scope, receive, send)

    async def _authenticate(self, token: str) -> Principal | None:
        # 形式の違う値は authenticate が DB を引く前に拒否する
        # （セッションは最初の照会まで接続しない）
        async with self.get_state().session_factory() as session, session.begin():
            if token.startswith(sync_tokens.TOKEN_PREFIX):
                sync = await sync_tokens.authenticate(session, token)
                if sync is None:
                    return None
                return Principal(sync.workspace, sync.token_id, sync.project_id)
            identity = await mcp_tokens.authenticate_identity(session, token)
            return Principal(identity.team, identity.token_id) if identity else None


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
    "Principal",
    "allowed_hosts",
    "build_mcp",
    "mount_mcp",
    "skill_body",
    "skill_instructions",
]
