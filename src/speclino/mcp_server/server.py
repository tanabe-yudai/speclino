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
- S75: `knowledge_pull`（書き出しの計画を作って置き、使い捨ての取得 URL を返す。計画の本文は
  応答で運ばない。エージェントはプラグインの `scripts/speclino_apply.py --plan-url` に渡す）と、
  `sync_status` の `pull_needed` / `reasons`。ワークスペースの MCP トークンと sync トークンの
  どちらでも呼べる（リポジトリは必ず `_workspace_project` で引き、lock のリポジトリ ID は引いた
  Project から入れる）。計画の上限は CLI と同じ 8MB
- S67: `spec_suggest`（AI の書き換え提案）。ワークスペースの MCP トークンだけが呼べる
  （sync トークンは読み取り専用なので拒否。LLM の費用を使うため）。
  返すのは本文の置換の提案と検証の要約だけで、IR は返さない
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
from typing import Any, cast, get_args
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from speclino.api.deps import AppState
from speclino.api.routes.spec_suggest import run_suggest
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
from speclino.domain.knowledge_sync import InstructionsFile, SkillsRoot, SyncLayout
from speclino.domain.run import GitRef
from speclino.services import knowledge_registry, knowledge_sync, mcp_tokens, sync_tokens
from speclino.services.mcp_context import ContextUnavailable, InProcessContextSource
from speclino.services.run_view import run_view
from speclino.services.spec_suggest import SuggestRefused

StateGetter = Callable[[], AppState]
MCP_PATH = "/mcp"
# BearerAuth がリクエストに載せる、トークンのワークスペース（slug）の置き場所
WORKSPACE_STATE_KEY = "mcp_workspace"
# 同じく、トークンの身元（`Principal`）の置き場所
PRINCIPAL_STATE_KEY = "mcp_principal"
# `sync_status` / `knowledge_pull` の引数の上限（lock の本文・申告のハッシュ）
MAX_LOCK_JSON_BYTES = 1024 * 1024
MAX_HASH_ENTRIES = 2000


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

    # lock_json は `str`（既定 ""）で受ける。`str | None` だと MCP の SDK が JSON に見える文字列を
    # 先に dict に解析してしまい、引数の検証で落ちる
    @mcp.tool()
    async def sync_status(
        repo_full_name: str,
        ctx: Context,
        lock_json: str = "",
        local_hashes: dict[str, str] | None = None,
        managed_inner: dict[str, str] | None = None,
        instructions_file: str = "",
        skills_dir: str = "",
    ) -> dict[str, Any]:
        """作業を始める前に、手元の知識を取り込み直す必要があるか（`pull_needed`）を返す。

        `lock_json` は手元の `.speclino/lock.json` の本文（文字列のまま。無ければ省く）。
        `local_hashes` は lock の `files` に載ったファイル（AGENTS.md / CLAUDE.md 以外）の
        今の sha256、`managed_inner` は AGENTS.md / CLAUDE.md の管理区画の中身の sha256（どちらも
        改行を LF に寄せた本文の `sha256:<hex>`、省略可）。`reasons` は no_lock / invalid_lock /
        expired / other_repo / members_changed / policy_changed / files_modified / stale_copy /
        layout_change。`pull_needed` なら `knowledge_pull` で取り込む。

        `instructions_file`（AGENTS.md か CLAUDE.md）と `skills_dir`（.agents/skills か
        .claude/skills）は `knowledge_pull` と同じ値を渡す。省略すると今の配置を保つ（初回は
        AGENTS.md と .agents/skills）。Claude Code は初回や配置を変えるとき、両方に
        instructions_file="CLAUDE.md", skills_dir=".claude/skills" を渡す。lock の配置と違えば
        `layout_change` で `pull_needed` になる。最終的な判定はゲートの照合（KNOWLEDGE-LOCK-001）。
        """
        principal = _principal(ctx)
        _check_inputs(lock_json, local_hashes, managed_inner)
        file_choice, root_choice = _check_layout(instructions_file, skills_dir)
        project, resolved = await _resolve(get_state(), principal, repo_full_name)
        return _sync_status(
            project,
            resolved,
            lock_json or None,
            local_hashes,
            managed_inner,
            file_choice,
            root_choice,
        )

    @mcp.tool()
    async def knowledge_pull(
        repo_full_name: str,
        ctx: Context,
        lock_json: str = "",
        instructions_file: str = "",
        skills_dir: str = "",
    ) -> dict[str, Any]:
        """このリポジトリに当たる承認済みの知識の書き出し計画を作り、使い捨ての取得 URL を返す。

        `lock_json` に手元の `.speclino/lock.json` の本文を必ず渡す（サーバが確かめたうえで、
        外れた知識の削除も計画する。渡さないと削除は計画されず古い知識が残りうる）。返すのは
        `plan_url`（1 回だけ・10 分で失効）・`expires_at`・`bundle_generation`・`summary`。
        リポジトリのルートで `python3 <speclino_apply.py のパス> --plan-url <plan_url>
        --expect-generation <bundle_generation>` を実行する（Claude Code のプラグインなら
        `${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py`、リポジトリに置いたなら相対パス。
        計画やファイルを自分で書かない）。

        書き出し先は取り込む側が選ぶ。`instructions_file`（AGENTS.md か CLAUDE.md）と
        `skills_dir`（.agents/skills か .claude/skills）は、省略すると今の配置を保つ
        （初回は AGENTS.md と .agents/skills）。Claude Code は初回（lock が無い・旧い配置のとき）に
        instructions_file="CLAUDE.md", skills_dir=".claude/skills" を指定する。それ以外の
        エージェントは省略してよい。配置を変えるときだけ指定する。応答の `layout` が実際の配置。
        """
        state = get_state()
        principal = _principal(ctx)
        _check_inputs(lock_json, None, None)
        file_choice, root_choice = _check_layout(instructions_file, skills_dir)
        project = await _workspace_project(state, principal, repo_full_name)
        try:
            async with state.session_factory() as session, session.begin():
                ticket = await knowledge_sync.issue_plan_ticket(
                    session,
                    workspace=principal.workspace,
                    project=project,
                    lock_json=lock_json or None,
                    channel=principal.channel,
                    token_id=principal.token_id,
                    instructions_file=file_choice,
                    skills_root=root_choice,
                )
        except LookupError as e:
            raise ToolError(f"{repo_full_name} はこのワークスペースに登録されていない") from e
        except ValueError as e:
            raise ToolError(f"知識を書き出せない: {e}") from e
        base = state.settings.public_url.rstrip("/")
        return {
            "plan_url": f"{base}/api/sync/plans/{ticket.token}",
            "expires_at": ticket.expires_at.isoformat(),
            "bundle_generation": ticket.bundle_generation,
            "layout": {
                "instructions_file": ticket.layout.instructions_file,
                "skills_dir": ticket.layout.skills_root,
            },
            "summary": {
                "ops": ticket.ops,
                "writes": ticket.writes,
                "deletes": ticket.deletes,
                "bytes": ticket.bytes,
            },
        }

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

    @mcp.tool()
    async def spec_suggest(
        markdown: str, ctx: Context, repo_full_name: str | None = None
    ) -> dict[str, Any]:
        """仕様の本文（Markdown と Mermaid の図）の書き換えの提案を返す（最大 10 件）。

        各提案は `start` / `end`（本文の文字オフセット、code point）を `replacement` に
        置き換える案と `reason`、当てた後に SpecLino の決定的な parser で検証し直した結果
        （`after`: 前後のエラー数・当てた後の診断）。提案は人が 1 件ずつ確かめて採否を決める。
        自動で当てない・保存しない。
        IR は返さない（本文から IR を作るのは parser だけ）。`repo_full_name` を渡すと、その
        リポジトリがこのワークスペースに登録されているかだけを確かめる。
        """
        state = get_state()
        principal = _principal(ctx)
        if principal.sync_project is not None:
            raise ToolError("sync トークンは読み取り専用で、AI の提案を使えない")
        if repo_full_name is not None:
            await _workspace_project(state, principal, repo_full_name)
        try:
            result = await run_suggest(state, markdown)
        except SuggestRefused as e:
            raise ToolError(str(e)) from e
        return result.model_dump(mode="json")

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


def _check_inputs(
    lock_json: str, local_hashes: dict[str, str] | None, managed_inner: dict[str, str] | None
) -> None:
    """引数の大きさの上限（lock の本文 1MB、申告のハッシュは 2000 件・1MB まで）。"""
    if len(lock_json.encode("utf-8")) > MAX_LOCK_JSON_BYTES:
        raise ToolError(f"lock_json が大きすぎる（{MAX_LOCK_JSON_BYTES} バイトまで）")
    for name, given in (("local_hashes", local_hashes), ("managed_inner", managed_inner)):
        if given is None:
            continue
        size = sum(len(k.encode("utf-8")) + len(v.encode("utf-8")) for k, v in given.items())
        if len(given) > MAX_HASH_ENTRIES or size > MAX_LOCK_JSON_BYTES:
            raise ToolError(f"{name} が大きすぎる（{MAX_HASH_ENTRIES} 件・1MB まで）")


def _check_layout(
    instructions_file: str, skills_dir: str
) -> tuple[InstructionsFile | None, SkillsRoot | None]:
    """配置の引数（省略は空文字）。列挙の値以外は拒否する。"""
    if instructions_file and instructions_file not in get_args(InstructionsFile):
        raise ToolError("instructions_file は AGENTS.md か CLAUDE.md")
    if skills_dir and skills_dir not in get_args(SkillsRoot):
        raise ToolError("skills_dir は .agents/skills か .claude/skills")
    return (
        cast(InstructionsFile, instructions_file) if instructions_file else None,
        cast(SkillsRoot, skills_dir) if skills_dir else None,
    )


def _normalize_hash(value: str) -> str:
    return value if value.startswith("sha256:") else "sha256:" + value.lower()


def _modified_files(
    lock: KnowledgeLock,
    local_hashes: dict[str, str] | None,
    managed_inner: dict[str, str] | None,
) -> list[str]:
    """エージェントが申告した手元のハッシュと lock の食い違い（申告が無ければ照合しない）。

    申告の対象（ファイル / 管理区画）のうち、lock の記録と違う・申告に無い（欠けている）・
    `absent` なのに申告がある（残っている）パス。
    """
    out: set[str] = set()
    for given, managed in ((local_hashes, False), (managed_inner, True)):
        if given is None:
            continue
        hashes = {p: _normalize_hash(h) for p, h in given.items()}
        for path, expected in lock.files.items():
            if (path in knowledge_sync.MANAGED_FILES) == managed and hashes.get(path) != expected:
                out.add(path)
        out |= {
            p for p in lock.absent if (p in knowledge_sync.MANAGED_FILES) == managed and p in hashes
        }
    return sorted(out)


def _declared_stale_copies(
    lock: KnowledgeLock | None,
    local_hashes: dict[str, str] | None,
    managed_inner: dict[str, str] | None,
) -> list[str]:
    """申告のハッシュに、lock が示す配置の外（別の指示書ファイル・別のルートの同名スキル）の
    ものがあれば、古い写しが残っている（ゲートの `stale_copy` と同じ導き方）。"""
    if lock is None:
        return []
    managed, dirs = knowledge_sync.stale_copy_probes(lock)
    out = [p for p in managed if managed_inner is not None and p in managed_inner]
    for d in dirs:
        if local_hashes is not None and any(p.startswith(d + "/") for p in local_hashes):
            out.append(d + "/")
    return sorted(out)


def _sync_status(
    project: Project,
    resolved: ResolvedKnowledge,
    lock_json: str | None,
    local_hashes: dict[str, str] | None = None,
    managed_inner: dict[str, str] | None = None,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
) -> dict[str, Any]:
    """ゲート（`KNOWLEDGE-LOCK-001`）と同じ `assess_lock` で判定し、申告のハッシュも照合する。"""
    generation = resolved.bundle.generation
    if project.github_repo_id is None:
        return {
            "current_generation": generation,
            "lock_generation": None,
            "stale": True,
            "pull_needed": False,
            "reasons": ["unregistered_repo"],
            "modified_files": [],
            "stale_copies": [],
            "verdict": "UNKNOWN",
            "reason": "リポジトリ ID が未登録のため照合できない（取り込みもできない）",
            "changed": [],
        }
    a = knowledge_sync.assess_lock(
        lock_json,
        current=resolved.bundle,
        current_members=knowledge_sync.project_members(resolved),
        github_repo_id=project.github_repo_id,
    )
    modified = _modified_files(a.lock, local_hashes, managed_inner) if a.lock is not None else []
    stale = _declared_stale_copies(a.lock, local_hashes, managed_inner)
    reasons = list(a.reasons)
    if modified and "files_modified" not in reasons:
        reasons.append("files_modified")
    if stale and "stale_copy" not in reasons:
        reasons.append("stale_copy")
    if _layout_changes(a.lock, instructions_file, skills_root):
        reasons.append("layout_change")
    verdict, message = a.verdict, a.message
    if stale and verdict == "VERIFIED_IN_SCOPE":  # ゲートと同じ（古い写しは REVIEW）
        verdict = "REVIEW"
        message = f"{knowledge_sync.STALE_COPY_MESSAGE}: {', '.join(stale)}"
    return {
        "current_generation": generation,
        "lock_generation": a.lock.bundle_generation if a.lock is not None else None,
        "stale": verdict != "VERIFIED_IN_SCOPE",
        "pull_needed": bool(reasons),
        "reasons": reasons,
        "modified_files": modified,
        "stale_copies": stale,
        "verdict": verdict,
        "reason": message,
        "changed": a.changed,
    }


def _layout_changes(
    lock: KnowledgeLock | None,
    instructions_file: InstructionsFile | None,
    skills_root: SkillsRoot | None,
) -> bool:
    """明示した配置が lock の配置と違うか（`knowledge_pull` と同じ `resolve_layout` の規則）。

    引数が無ければ今の配置を保つので変更ではない。lock が agents / claude でなければ（旧い配置・
    食い違い）、明示した時点で変更として取り込み直す。lock に指示書もスキルも無ければ比べない。
    """
    if lock is None or (instructions_file is None and skills_root is None):
        return False
    kind = knowledge_sync.infer_layout(lock.files)
    if kind == "unknown":
        return False
    desired = knowledge_sync.resolve_layout(
        lock, instructions_file=instructions_file, skills_root=skills_root
    )
    current = {
        "agents": knowledge_sync.DEFAULT_LAYOUT,
        "claude": SyncLayout("CLAUDE.md", ".claude/skills"),
    }.get(kind)
    return desired != current


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
