"""知識の配信（S68・S75）。

3 つの面を持つ:

- サーバ: 対象のプロジェクトに当たる bundle の内容を、書き出すファイルに組み立て（`render_files`）、
  署名つきの lock を作り（`issue_lock`）、書き出しの計画（`SyncPlan`）にして署名する
  （`plan_writes` / `build_plan`）。削除は、署名・ワークスペース・リポジトリ ID を確かめた前回の
  lock（`verified_previous_lock`）からだけ計画する。`plan_for_project` が REST（sync トークン）と
  MCP（`knowledge_pull`）の要求に答え、`knowledge_fetches` に記録する
- クライアント: 計画を当てるのは標準ライブラリだけの `speclino.sync_apply`（プラグインの
  `scripts/speclino_apply.py` と同じもの）。パス・管理区画・ハッシュの関数もそこを正本にする
- ゲート: `check_lock_snapshot` / `check_lock` が lock の署名・期限・ファイルのハッシュ・
  消したはずのファイル（`absent`）・世代を確かめ、`KNOWLEDGE-LOCK-001`（必須）の Finding を 1 つ返す

書き出しの約束（S76・S77）:

- skill: 配置（`SyncLayout`）の `skill_roots`（`.agents/skills` と `.claude/skills` の部分集合）の
  それぞれの `<name>/` に `SKILL.md` と付属ファイル（同じ内容）。ルートの集合は前回の lock の
  `skill_roots`（無ければパスから推定）に取り込むエージェントの名乗りのルートを足したもの
  （claude 系なら `.claude/skills`、それ以外・不明は `.agents/skills`）。`skills_dir` の明示は排他
- instructions: 本文は `AGENTS.md` の管理区画（`instructions_file="CLAUDE.md"` の明示時だけ
  `CLAUDE.md`）。全ての指示書を workspace スコープ → プロジェクトスコープ、それぞれ名前順に連結。
  本文が AGENTS.md のときは、CLAUDE.md が手元にあれば管理区画に橋 `@AGENTS.md` を書く
  （`if_exists` の op。作らない。lock の `files` には載せない）
- spec: `.speclino/specs/<name>.md`（`markdown` の写し）

- 管理区画は `<!-- speclino:begin bundle=<n> -->` 改行、中身、`<!-- speclino:end -->`。区画の外は
  利用者のもので、読み書きしない（区画の置き換えだけ）
- lock の `files` は「書き出した中身」の sha256。`AGENTS.md` / `CLAUDE.md` は区画の中身だけ。
  改行は LF に寄せてからハッシュする（Windows の checkout の autocrlf で偽の改変にしないため）
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from speclino import sync_apply
from speclino.db.models import McpTokenRecord, Project, SyncPlanRecord, SyncTokenRecord
from speclino.db.repositories.sync_tokens import KnowledgeFetchRepository, SyncPlanRepository
from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Severity, Verdict
from speclino.domain.knowledge import (
    ArtifactRevision,
    Bundle,
    BundleMember,
    InstructionsContent,
    KnowledgeLock,
    ResolvedKnowledge,
    SkillContent,
    SpecContent,
)
from speclino.domain.knowledge_sync import (
    AGENT_NAME_PATTERN,
    DeleteOp,
    InstructionsFile,
    RemoveManagedBlockOp,
    SetManagedBlockOp,
    SkillsRoot,
    SyncIdentity,
    SyncLayout,
    SyncOp,
    SyncPlan,
    WriteOp,
)
from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr
from speclino.sync_apply import (
    LOCK_PATH,
    SKILL_ROOTS,
    SPEC_ROOT,
    ManagedBlock,
    ManagedBlockError,
    file_hash,
    merge_managed,
    remove_managed,
    split_managed,
    validate_rel_path,
)
from speclino.verifiers.base import Snapshot
from speclino.verifiers.snapshot import DirectorySnapshot

LOCK_REQUIREMENT_ID = "KNOWLEDGE-LOCK-001"
# CLAUDE.md の管理区画に書く橋（S77）。Claude Code は CLAUDE.md があると AGENTS.md を読まないので、
# 既にある CLAUDE.md から import させる（HTML コメントの印は除去され、この行は有効）
BRIDGE_PATH = "CLAUDE.md"
BRIDGE_INNER = "@AGENTS.md\n"
BRIDGE_HASH = sync_apply.file_hash(BRIDGE_INNER)
LOCK_TTL = timedelta(days=30)
MANAGED_FILES = sync_apply.MANAGED_FILES
DEFAULT_LAYOUT = SyncLayout()

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 書き出しを拒否した（パス・symlink・管理区画の不正・上限など）。手元の状態と lock は前のまま
SyncRejected = sync_apply.PreconditionFailed


def plan_json_bytes(plan: SyncPlan) -> int:
    """計画の JSON の大きさ（MCP の 1 回の応答の上限の判定に使う）。"""
    return len(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))


# 後から足した欄。None・空のときは署名の対象に含めない（欄の無い古い lock の署名を保つ）
_OPTIONAL_LOCK_FIELDS = ("policy_revision", "policy_hash", "key_id", "absent", "skill_roots")


def lock_payload(lock: KnowledgeLock) -> bytes:
    """lock の署名の対象（`signature` 以外の正規 JSON。後から足した欄は値があるときだけ）。"""
    data = lock.model_dump(mode="json", exclude={"signature"})
    for name in _OPTIONAL_LOCK_FIELDS:
        if data.get(name) in (None, []):
            data.pop(name, None)
    return kr.canonical_json(data)


def verify_lock(lock: KnowledgeLock) -> bool:
    return bundle_signing.verify(lock_payload(lock), lock.signature, lock.key_id)


def plan_payload(plan: SyncPlan) -> bytes:
    """計画の署名の対象（`signature` 以外の正規 JSON）。"""
    return kr.canonical_json(plan.model_dump(mode="json", exclude={"signature"}))


def verify_plan(plan: SyncPlan) -> bool:
    return bundle_signing.verify(plan_payload(plan), plan.signature, plan.key_id)


# ---------------------------------------------------------------------------
# サーバ: 書き出すファイルと lock
# ---------------------------------------------------------------------------


def _skill_dir(rev: ArtifactRevision) -> str:
    # 書き出し先の名前は bundle の競合検査と同じ（frontmatter の name、無ければ artifact 名）
    name = kr.skill_dir_name(rev)
    if not _NAME.match(name):
        raise ValueError(f"スキル {rev.name} の名前 {name!r} はディレクトリ名に使えない")
    return name


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def render_files(
    resolved: ResolvedKnowledge, layout: SyncLayout = DEFAULT_LAYOUT
) -> dict[str, str]:
    """プロジェクトに当たる知識から、書き出すファイル（相対パス → 本文）を作る。決定的な順序。

    指示書は全て `layout.instructions_file` に、スキルは `layout.skill_roots` のそれぞれの下に書く。
    橋（CLAUDE.md の `@AGENTS.md`）はここに含めない（`build_plan` が足す）。
    """
    files: dict[str, str] = {}
    for rev in sorted(resolved.skills, key=lambda r: r.name):
        assert isinstance(rev.content, SkillContent)
        name = _skill_dir(rev)
        for root in layout.skill_roots:
            files[f"{root}/{name}/SKILL.md"] = rev.content.skill_md
            for rel, body in sorted(rev.content.files.items()):
                files[f"{root}/{name}/{rel}"] = body
    bodies = [
        _with_newline(r.content.body)
        for r in sorted(
            resolved.instructions, key=lambda r: (not r.content.scope.workspace, r.name)
        )
        if isinstance(r.content, InstructionsContent)
    ]
    if bodies:
        files[layout.instructions_file] = "\n".join(bodies)
    for rev in sorted(resolved.specs, key=lambda r: r.name):
        assert isinstance(rev.content, SpecContent)
        files[f"{SPEC_ROOT}/{rev.name}.md"] = rev.content.markdown
    for path in files:
        validate_rel_path(path)
    return files


_SKILL_ROOTS = cast(tuple[SkillsRoot, ...], tuple(sorted(SKILL_ROOTS)))  # 昇順


def lock_skill_roots(lock: KnowledgeLock | None) -> tuple[SkillsRoot, ...]:
    """前回のスキルのルートの集合（昇順）。lock の `skill_roots`、欄の無い古い lock は `files` の
    パスから推定する。推定できなければ（lock なし・スキルが無い古い lock）空。"""
    if lock is None:
        return ()
    if lock.skill_roots:
        return tuple(lock.skill_roots)  # 署名済みの lock は昇順・重複なしで書いている
    return tuple(r for r in _SKILL_ROOTS if any(p.startswith(r + "/") for p in lock.files))


def instructions_files(lock: KnowledgeLock) -> list[str]:
    """lock が本文を載せている指示書のファイル（AGENTS.md / CLAUDE.md。旧い lock は両方もある）。"""
    return [f for f in MANAGED_FILES if f in lock.files]


_AGENT_NAME = re.compile(AGENT_NAME_PATTERN)


def is_valid_agent(name: str) -> bool:
    """引数で渡された名乗りが使える形か（`AGENT_NAME_PATTERN`。整形はしない）。"""
    return _AGENT_NAME.match(name) is not None


_AGENT_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


def normalize_agent(name: str | None) -> str | None:
    """名乗り（MCP の clientInfo の name など）を応答に出せる形にする。使えない文字は落とし、
    64 文字で切る。空なら None。"""
    if not name:
        return None
    cleaned = _AGENT_UNSAFE.sub("", name).strip()[:64].strip()
    return cleaned or None


def agent_skills_root(agent: str | None) -> SkillsRoot:
    """名乗りのスキルのルート。名前に claude を含めば（大小無視）`.claude/skills`、それ以外・
    不明は `.agents/skills`。自己申告で信頼しないが、変わるのは書き出し先だけ。"""
    return ".claude/skills" if agent and "claude" in agent.lower() else ".agents/skills"


def resolve_layout(
    previous: KnowledgeLock | None,
    *,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
) -> SyncLayout:
    """取り込みの配置（S77）。

    - 指示書の本文: 明示があればそれ、無ければ AGENTS.md（前回の配置は保たない。CLAUDE.md は橋）
    - スキル: `skills_root` の明示は排他（そのルートだけ）。無ければ前回のルート
      （`lock_skill_roots`）に名乗りのルート（`agent_skills_root`）を足す（和集合。減らさない）
    """
    if skills_root is not None:
        roots: tuple[SkillsRoot, ...] = (skills_root,)
    else:
        roots = tuple(sorted({*lock_skill_roots(previous), agent_skills_root(agent)}))
    return SyncLayout(instructions_file or "AGENTS.md", roots, agent)


def layout_changes(
    lock: KnowledgeLock | None,
    *,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
) -> bool:
    """取り込み直すと配置が変わるか（`sync_status` の `layout_change`。`resolve_layout` の規則）。

    スキルは前回のルートが分かるときだけ比べる（スキルの無い古い lock は比べない。次の取り込みで
    lock に残る）。指示書は lock が本文を載せていて、その置き場所が求める置き場所と違うとき。
    """
    if lock is None:
        return False
    desired = resolve_layout(
        lock, instructions_file=instructions_file, skills_root=skills_root, agent=agent
    )
    current = lock_skill_roots(lock)
    if current and current != desired.skill_roots:
        return True
    body = instructions_files(lock)
    return bool(body) and body != [desired.instructions_file]


def project_members(resolved: ResolvedKnowledge) -> list[BundleMember]:
    ids = {r.artifact_id for r in (*resolved.skills, *resolved.instructions, *resolved.specs)}
    return sorted(
        (m for m in resolved.bundle.members if m.artifact_id in ids),
        key=lambda m: (m.kind, m.name),
    )


def issue_lock(
    resolved: ResolvedKnowledge,
    *,
    github_repo_id: int,
    files: dict[str, str],
    absent: Iterable[str] = (),
    skill_roots: Iterable[SkillsRoot] = (),
    now: datetime | None = None,
) -> KnowledgeLock:
    """署名つきの lock。`files` は `render_files` の結果（ハッシュは書き出した中身のもの）。

    `absent` は前回あって今回は無いパス（`removed_paths`）。ゲートが残っていないことを照合する。
    `skill_roots` は配置のスキルのルート（スキルが 0 件でも残し、次の取り込みで和集合を忘れない）。
    """
    issued = now or datetime.now(UTC)
    unsigned = KnowledgeLock(
        workspace=resolved.bundle.workspace,
        github_repo_id=github_repo_id,
        bundle_generation=resolved.bundle.generation,
        policy_revision=resolved.bundle.policy_revision,
        policy_hash=resolved.bundle.policy_hash,
        members=project_members(resolved),
        files={p: file_hash(body) for p, body in sorted(files.items())},
        absent=sorted(absent),
        skill_roots=sorted(set(skill_roots)),
        issued_at=issued,
        expires_at=issued + LOCK_TTL,
        key_id=bundle_signing.current_key_id(),
        signature="unsigned",
    )
    return unsigned.model_copy(update={"signature": bundle_signing.sign(lock_payload(unsigned))})


def verified_previous_lock(
    lock_json: str | None, *, workspace: str, github_repo_id: int
) -> KnowledgeLock | None:
    """削除の根拠に使える前回の lock。署名・ワークスペース・リポジトリ ID が合わなければ None。

    期限切れでも署名が合えばサーバが書いた本物なので根拠に使う（期限は鮮度の判定の話）。
    """
    if not lock_json:
        return None
    try:
        lock = KnowledgeLock.model_validate_json(lock_json)
    except (ValidationError, ValueError):
        return None
    if not verify_lock(lock):
        return None
    if lock.workspace != workspace or lock.github_repo_id != github_repo_id:
        return None
    return lock


def removed_paths(files: dict[str, str], previous: KnowledgeLock | None) -> list[str]:
    """前回の lock にあって今回は無いパス（新しい lock の `absent`、削除の対象）。"""
    if previous is None:
        return []
    out = []
    for path in sorted(set(previous.files) - set(files)):
        try:
            validate_rel_path(path)
        except SyncRejected:
            continue  # 署名済みの lock に不正なパスは無いはずだが、消す根拠にはしない
        out.append(path)
    return out


def plan_writes(
    files: dict[str, str],
    lock: KnowledgeLock,
    previous: KnowledgeLock | None,
    *,
    repo_full_name: str,
    cleanup: Iterable[str] = (),
    bridge: bool = False,
) -> SyncPlan:
    """書き出しの計画を作って署名する（純粋）。`previous` は `verified_previous_lock` の結果だけ。

    - `files` の各パスは `write`（AGENTS.md / CLAUDE.md は `set_managed_block`）。前回の lock の
      記録があれば `expected_*` に入れる（実行スクリプトは記録どおりの手元のファイルだけ上書きする）
    - `lock.absent` の各パスは `delete`（AGENTS.md / CLAUDE.md は `remove_managed_block`）。根拠は
      前回の lock の記録だけ（`previous` が無ければ `absent` は空でなければならない）
    - `cleanup` の AGENTS.md / CLAUDE.md（選ばれなかった側）は、期待ハッシュ無しの
      `remove_managed_block`（印が本物なら SpecLino のものなので前回の lock が無くても取り除く。
      lock の `absent` には載せない）
    - `bridge` なら CLAUDE.md に橋（`if_exists` の `set_managed_block`、lock に載せない）。前回の
      lock が CLAUDE.md に本文を載せていれば、そのハッシュを期待値にする（旧配置からの移行）
    - CLAUDE.md に本文を書くときは、前回の記録が無くても区画の中身が橋なら置き換える
    - 上限・パスは実行スクリプトと同じ `sync_apply.check_ops` で確かめる（超えれば `SyncRejected`）
    """
    if set(files) != set(lock.files):
        raise SyncRejected("files と lock のパスが一致しない")
    before = previous.files if previous is not None else {}
    if any(path not in before for path in lock.absent):
        raise SyncRejected("absent は検証済みの前回の lock に載ったパスだけ")
    ops: list[SyncOp] = []
    for path in sorted(files):
        if path in MANAGED_FILES:
            expected = before.get(path) or (BRIDGE_HASH if path == BRIDGE_PATH else None)
            ops.append(
                SetManagedBlockOp(
                    path=path,
                    inner=files[path],
                    generation=lock.bundle_generation,
                    expected_inner_sha256=expected,
                )
            )
        else:
            ops.append(
                WriteOp(
                    path=path,
                    content=files[path],
                    sha256=lock.files[path],
                    expected_sha256=before.get(path),
                )
            )
    for path in sorted(lock.absent):
        if path in MANAGED_FILES:
            ops.append(RemoveManagedBlockOp(path=path, expected_inner_sha256=before[path]))
        else:
            ops.append(DeleteOp(path=path, expected_sha256=before[path]))
    for path in sorted(set(cleanup)):
        if (
            path not in MANAGED_FILES
            or path in files
            or path in lock.absent
            or (bridge and path == BRIDGE_PATH)
        ):
            raise SyncRejected(f"片付けの対象にできない: {path}")
        ops.append(RemoveManagedBlockOp(path=path))
    if bridge:
        if "AGENTS.md" not in files or BRIDGE_PATH in files or BRIDGE_PATH in lock.absent:
            raise SyncRejected("橋は本文が AGENTS.md だけのときに限る")
        ops.append(
            SetManagedBlockOp(
                path=BRIDGE_PATH,
                inner=BRIDGE_INNER,
                generation=lock.bundle_generation,
                expected_inner_sha256=before.get(BRIDGE_PATH),
                if_exists=True,
            )
        )
    sync_apply.check_ops([op.model_dump(mode="json") for op in ops])
    unsigned = SyncPlan(
        workspace=lock.workspace,
        repo_full_name=repo_full_name,
        github_repo_id=lock.github_repo_id,
        bundle_generation=lock.bundle_generation,
        ops=ops,
        lock=lock,
        key_id=bundle_signing.current_key_id(),
        signature="unsigned",
    )
    return unsigned.model_copy(update={"signature": bundle_signing.sign(plan_payload(unsigned))})


def build_plan(
    resolved: ResolvedKnowledge,
    *,
    github_repo_id: int,
    repo_full_name: str,
    previous: KnowledgeLock | None,
    layout: SyncLayout | None = None,
    now: datetime | None = None,
) -> SyncPlan:
    """プロジェクトに当たる知識から、書き出すファイル・lock（`absent`・`skill_roots`）・計画を作る。

    `layout` が無ければ前回の lock から決める（`resolve_layout`）。

    - 本文が AGENTS.md なら CLAUDE.md に橋を書く（手元にあるときだけ）。前回 CLAUDE.md に本文が
      あっても `absent` に入れず、橋が前回の本文を置き換える（S77 の規則 8）
    - それ以外で本文を書かない指示書ファイル（本文が CLAUDE.md なら AGENTS.md、指示書が 0 件なら
      両方）の管理区画は、前回の lock が消す対象にしていなければ（lock の有無を問わず）片付ける
    - 上限を超えたら、スキルを複数のルートに書くときは `skills_dir` で片側を指定するよう促す
    """
    layout = layout or resolve_layout(previous)
    files = render_files(resolved, layout)
    bridge = layout.instructions_file == "AGENTS.md" and "AGENTS.md" in files
    absent = [p for p in removed_paths(files, previous) if not (bridge and p == BRIDGE_PATH)]
    lock = issue_lock(
        resolved,
        github_repo_id=github_repo_id,
        files=files,
        absent=absent,
        skill_roots=layout.skill_roots,
        now=now,
    )
    cleanup = [
        f
        for f in MANAGED_FILES
        if f not in files and f not in lock.absent and not (bridge and f == BRIDGE_PATH)
    ]
    try:
        return plan_writes(
            files, lock, previous, repo_full_name=repo_full_name, cleanup=cleanup, bridge=bridge
        )
    except sync_apply.LimitExceeded as e:
        if len(layout.skill_roots) < 2:
            raise
        raise sync_apply.LimitExceeded(f"{e}。{_TWO_ROOTS_HINT}") from e


_TWO_ROOTS_HINT = (
    "スキルを .agents/skills と .claude/skills の両方に書き出すため上限を超えた。"
    "skills_dir（`speclino sync --skills-dir`）で片側を指定して取り込み直す"
)


class PlanTooLarge(ValueError):
    """計画が上限（`sync_apply.MAX_PLAN_BYTES`、CLI・MCP 共通）を超えた。"""


PLAN_URL_TTL = timedelta(minutes=10)
PLAN_TOKEN_PREFIX = "slplan_"


async def _plan_for(
    session: AsyncSession,
    *,
    workspace: str,
    project: Project,
    lock_json: str | None,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
) -> tuple[SyncPlan, SyncLayout]:
    if project.github_repo_id is None:
        raise ValueError("リポジトリ ID が未登録のため書き出せない")
    bundle = await kr.ensure_bundle(session, workspace)
    resolved = await kr.resolve_for_project(session, bundle, str(project.id))
    previous = verified_previous_lock(
        lock_json, workspace=workspace, github_repo_id=project.github_repo_id
    )
    layout = resolve_layout(
        previous, instructions_file=instructions_file, skills_root=skills_root, agent=agent
    )
    plan = build_plan(
        resolved,
        github_repo_id=project.github_repo_id,
        repo_full_name=project.repo_full_name,
        previous=previous,
        layout=layout,
    )
    size = plan_json_bytes(plan)
    if size > sync_apply.MAX_PLAN_BYTES:
        hint = f"。{_TWO_ROOTS_HINT}" if len(layout.skill_roots) > 1 else ""
        raise PlanTooLarge(
            f"計画が {sync_apply.MAX_PLAN_BYTES} バイトを超える（{size} バイト）{hint}"
        )
    return plan, layout


async def plan_for_project(
    session: AsyncSession,
    *,
    workspace: str,
    project: Project,
    lock_json: str | None,
    channel: str,
    token_id: uuid.UUID | None,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
) -> SyncPlan:
    """project（呼び出し側がテナントを確かめたもの）に当たる今の bundle の計画を作り、記録する。

    lock のリポジトリ ID は要求の値ではなく project のものを使う。上限を超えれば
    `PlanTooLarge`（記録しない）。bundle を読めなければ ValueError / LookupError。
    """
    plan, _ = await _plan_for(
        session,
        workspace=workspace,
        project=project,
        lock_json=lock_json,
        instructions_file=instructions_file,
        skills_root=skills_root,
        agent=agent,
    )
    await KnowledgeFetchRepository(session).record(
        workspace=workspace,
        channel=channel,
        token_id=token_id,
        bundle_generation=plan.bundle_generation,
        project_id=project.id,
        artifact_ids=[m.artifact_id for m in plan.lock.members],
    )
    return plan


@dataclass(frozen=True)
class PlanTicket:
    """`knowledge_pull` の応答。計画の本文は持たず、使い捨ての取得トークンを持つ。"""

    token: str
    expires_at: datetime
    bundle_generation: int
    ops: int
    writes: int
    deletes: int
    bytes: int
    layout: SyncLayout = DEFAULT_LAYOUT


async def issue_plan_ticket(
    session: AsyncSession,
    *,
    workspace: str,
    project: Project,
    lock_json: str | None,
    channel: str,
    token_id: uuid.UUID | None,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
    now: datetime | None = None,
) -> PlanTicket:
    """計画を作って `sync_plans` に置き、1 回だけ・10 分で失効する取得トークンを返す。

    保存するのはトークンの SHA-256 だけ。取得の記録（`knowledge_fetches`）は実際に取得したとき
    （`redeem_plan_ticket`）に残す。
    """
    plan, layout = await _plan_for(
        session,
        workspace=workspace,
        project=project,
        lock_json=lock_json,
        instructions_file=instructions_file,
        skills_root=skills_root,
        agent=agent,
    )
    issued = now or datetime.now(UTC)
    token = PLAN_TOKEN_PREFIX + secrets.token_urlsafe(32)
    body = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    assert project.github_repo_id is not None  # _plan_for が確かめた
    await SyncPlanRepository(session).create(
        SyncPlanRecord(
            token_hash=_ticket_hash(token),
            workspace=workspace,
            project_id=project.id,
            github_repo_id=project.github_repo_id,
            bundle_generation=plan.bundle_generation,
            channel=channel,
            token_id=token_id,
            artifact_ids=[m.artifact_id for m in plan.lock.members],
            plan_json=body,
            expires_at=issued + PLAN_URL_TTL,
        ),
        now=issued,
    )
    # 期待ハッシュ無しの remove_managed_block は配置の片付け（書き込みでも削除でもない）
    deletes = sum(
        op.op == "delete"
        or (op.op == "remove_managed_block" and op.expected_inner_sha256 is not None)
        for op in plan.ops
    )
    cleanups = sum(
        op.op == "remove_managed_block" and not op.expected_inner_sha256 for op in plan.ops
    )
    return PlanTicket(
        token=token,
        expires_at=issued + PLAN_URL_TTL,
        bundle_generation=plan.bundle_generation,
        ops=len(plan.ops),
        writes=len(plan.ops) - deletes - cleanups,
        deletes=deletes,
        bytes=len(body.encode("utf-8")),
        layout=layout,
    )


def _ticket_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _issuer_still_valid(session: AsyncSession, record: SyncPlanRecord) -> bool:
    """計画を発行させたトークンが今も有効か（失効していない・同じワークスペース・sync トークンは
    同じプロジェクト）。トークンを取り消したら、取り消し前に作った計画も渡さない。"""
    if record.token_id is None:
        return False
    if record.channel == "sync":
        sync = await session.get(SyncTokenRecord, record.token_id)
        return (
            sync is not None
            and sync.revoked_at is None
            and sync.team == record.workspace
            and sync.project_id == record.project_id
        )
    mcp = await session.get(McpTokenRecord, record.token_id)
    return mcp is not None and mcp.revoked_at is None and mcp.team == record.workspace


async def _ticket_still_valid(session: AsyncSession, record: SyncPlanRecord) -> bool:
    """計画の取得と実行スクリプトの配信が共有する検証（リポジトリが付け替わっていない・
    発行元のトークンが有効）。"""
    project = await session.get(Project, record.project_id)
    return (
        project is not None
        and project.team == record.workspace
        and project.github_repo_id == record.github_repo_id
        and await _issuer_still_valid(session, record)
    )


async def redeem_plan_ticket(
    session: AsyncSession, token: str, *, now: datetime | None = None
) -> str | None:
    """取得トークンの計画（JSON の本文）。無い・期限切れ・取得済み・リポジトリが変わった・発行元の
    トークンが取り消されたなら None。

    1 回だけ渡す（取得済みにしてから返す）。取得を `knowledge_fetches` に記録する。
    """
    if not token.startswith(PLAN_TOKEN_PREFIX) or len(token) > 100:
        return None
    record = await SyncPlanRepository(session).consume(
        _ticket_hash(token), now=now or datetime.now(UTC)
    )
    if record is None:
        return None
    if not await _ticket_still_valid(session, record):
        return None
    await KnowledgeFetchRepository(session).record(
        workspace=record.workspace,
        channel=record.channel,
        token_id=record.token_id,
        bundle_generation=record.bundle_generation,
        project_id=record.project_id,
        artifact_ids=list(record.artifact_ids),
    )
    return record.plan_json


async def fetch_for_sync(
    session: AsyncSession,
    identity: SyncIdentity,
    github_repo_id: int,
    lock_json: str | None = None,
    instructions_file: InstructionsFile | None = None,
    skills_root: SkillsRoot | None = None,
    agent: str | None = None,
) -> SyncPlan:
    """sync トークンの要求に答える。トークンのプロジェクトのリポジトリでなければ `LookupError`。"""
    project = await session.get(Project, identity.project_id)
    if (
        project is None
        or project.team != identity.workspace
        or project.github_repo_id != github_repo_id
    ):
        raise LookupError("このトークンのリポジトリではない")
    return await plan_for_project(
        session,
        workspace=identity.workspace,
        project=project,
        lock_json=lock_json,
        channel="sync",
        token_id=identity.token_id,
        instructions_file=instructions_file,
        skills_root=skills_root,
        agent=agent,
    )


# ---------------------------------------------------------------------------
# ゲート: lock の鮮度
# ---------------------------------------------------------------------------


_CATEGORY: dict[Verdict, Category] = {
    "UNKNOWN": "unsupported_analysis",
    "REVIEW": "stale_approval",
    "VERIFIED_IN_SCOPE": "conformant",
    "VIOLATED": "constraint_mismatch",
}


def _finding(
    snapshot: Snapshot,
    verdict: Verdict,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
) -> Finding:
    severity: Severity = "info" if verdict == "VERIFIED_IN_SCOPE" else "high"
    return Finding(
        id=f"knowledge:{LOCK_REQUIREMENT_ID}",
        requirement_id=LOCK_REQUIREMENT_ID,
        behavior_id="knowledge:lock",
        verdict=verdict,
        category=_CATEGORY[verdict],
        severity=severity,
        message=message,
        expected=expected,
        actual=actual,
        claim_scope="persisted_state",
        code_refs=[SourceRef(snapshot_id=snapshot.snapshot_id, path=LOCK_PATH)],
    )


def _member_keys(members: Iterable[BundleMember]) -> set[tuple[str, int, str]]:
    return {(m.artifact_id, m.revision, m.content_hash) for m in members}


def changed_members(
    locked: list[BundleMember], current: list[BundleMember]
) -> list[dict[str, Any]]:
    """lock と今の間で変わった知識（追加・削除・改訂）。kind・名前の順。"""
    before = {m.artifact_id: m for m in locked}
    after = {m.artifact_id: m for m in current}
    out: list[dict[str, Any]] = []
    for aid in set(before) | set(after):
        old, new = before.get(aid), after.get(aid)
        if old is not None and new is not None and old.content_hash == new.content_hash:
            continue
        ref = new or old
        assert ref is not None
        out.append(
            {
                "name": ref.name,
                "kind": ref.kind,
                "lock_revision": old.revision if old else None,
                "current_revision": new.revision if new else None,
            }
        )
    return sorted(out, key=lambda c: (c["kind"], c["name"]))


def _changed_files(snapshot: Snapshot, lock: KnowledgeLock) -> list[str]:
    changed = []
    for path, expected in sorted(lock.files.items()):
        try:
            text = snapshot.read_text(path)
            if text is not None and path in MANAGED_FILES:
                block = split_managed(text)
                # begin 行の世代が lock と違えば、区画を手で差し替えたものとみなす
                ok = block is not None and block.generation == lock.bundle_generation
                text = block.inner if ok and block is not None else None
        except (UnicodeDecodeError, ManagedBlockError, OSError):
            text = None
        if text is None or file_hash(text) != expected:
            changed.append(path)
    return changed


def _leftover_absent(snapshot: Snapshot, lock: KnowledgeLock) -> list[str]:
    """lock の `absent`（消したはずのファイル・取り除いたはずの管理区画）のうち、残っているもの。

    AGENTS.md / CLAUDE.md は区画が無ければよい（区画の外は利用者のもの）。読めない・印が壊れて
    いて確かめられないものも残っている扱い（フェイルクローズ）。
    """
    left = []
    for path in lock.absent:
        try:
            text = snapshot.read_text(path)
            if text is None or (path in MANAGED_FILES and split_managed(text) is None):
                continue
        except (UnicodeDecodeError, ManagedBlockError, OSError):
            pass
        left.append(path)
    return left


def _unlisted_skill_files(snapshot: Snapshot, lock: KnowledgeLock) -> list[str]:
    """lock に載ったスキルのディレクトリにある、lock に無いファイル。"""
    dirs = set()
    for path in lock.files:
        for root in SKILL_ROOTS:
            if path.startswith(root + "/"):
                dirs.add(root + "/" + path[len(root) + 1 :].split("/", 1)[0])
    extra: list[str] = []
    for d in sorted(dirs):
        extra += [p for p in snapshot.glob(f"{d}/**/*") if p not in lock.files]
    return sorted(set(extra))


@dataclass(frozen=True)
class CopyProbes:
    """lock から導く、古い写しや橋の欠けを探す場所（S76・S77）。

    - `no_block`: 管理区画があってはならない指示書ファイル（本文を載せていない側。指示書が 0 件の
      lock は両方）
    - `bridge`: 本文が AGENTS.md だけの lock の CLAUDE.md。区画があれば中身は橋でなければならず
      （それ以外は古い写し）、ファイルがあるのに区画が無ければ橋の欠け
    - `skill_dirs`: 存在してはならないスキルのディレクトリ（lock のスキルと同名で、そのスキルを
      lock が載せていないルート。`skill_roots` の外のルートを含む）
    """

    no_block: list[str]
    bridge: str | None
    skill_dirs: list[str]


def stale_copy_probes(lock: KnowledgeLock) -> CopyProbes:
    """lock から導く、古い写しが残っていてはならない場所（ゲートと `sync_status` が使う）。

    本文を両方のファイルに載せた旧い lock は、lock 自身が両方を照合する（探す場所は無い）。
    """
    body = instructions_files(lock)
    bridge = BRIDGE_PATH if body == ["AGENTS.md"] else None
    no_block = [f for f in MANAGED_FILES if f not in body and f != bridge] if len(body) < 2 else []
    names: dict[str, set[str]] = {}
    for path in lock.files:
        for root in SKILL_ROOTS:
            if path.startswith(root + "/") and path.count("/") >= 3:
                names.setdefault(path.split("/")[2], set()).add(root)
    dirs = [
        f"{root}/{name}"
        for name, roots in sorted(names.items())
        for root in sorted(SKILL_ROOTS)
        if root not in roots
    ]
    return CopyProbes(no_block, bridge, dirs)


def _block_inner(snapshot: Snapshot, path: str) -> tuple[bool, str | None]:
    """(ファイルがあるか, 管理区画の中身)。読めない・印が壊れていれば `ManagedBlockError`。"""
    try:
        text = snapshot.read_text(path)
    except (UnicodeDecodeError, OSError) as e:
        raise ManagedBlockError(f"{path} を読めない") from e
    if text is None:
        return False, None
    block = split_managed(text)
    return True, block.inner if block is not None else None


def _stale_copies(snapshot: Snapshot, lock: KnowledgeLock) -> tuple[list[str], bool]:
    """`stale_copy_probes` の場所に残っている古い写しと、橋の欠け（CLAUDE.md があるのに区画が
    無い）。読めない・印が壊れていれば残っている扱い（フェイルクローズ）。"""
    probes = stale_copy_probes(lock)
    left = []
    bridge_missing = False
    for path in probes.no_block:
        try:
            if _block_inner(snapshot, path)[1] is None:
                continue
        except ManagedBlockError:
            pass
        left.append(path)
    if probes.bridge is not None:
        try:
            exists, inner = _block_inner(snapshot, probes.bridge)
            if exists and inner is None:
                bridge_missing = True
            elif inner is not None and file_hash(inner) != BRIDGE_HASH:
                left.append(probes.bridge)
        except ManagedBlockError:
            left.append(probes.bridge)
    for d in probes.skill_dirs:
        try:
            if snapshot.glob(f"{d}/**/*"):
                left.append(d + "/")
        except OSError:
            left.append(d + "/")
    return left, bridge_missing


def declared_copies(
    lock: KnowledgeLock | None,
    local_hashes: dict[str, str] | None,
    managed_inner: dict[str, str] | None,
) -> tuple[list[str], bool]:
    """エージェントの申告（`sync_status`）から、ゲートと同じ導き方で古い写しと橋の欠けを返す。

    `managed_inner` のキーはファイルがあること、値は管理区画の中身のハッシュ（区画が無ければ空）。
    申告が無い側は照合しない。
    """
    if lock is None:
        return [], False
    probes = stale_copy_probes(lock)
    inner = {p: normalize_hash(h) for p, h in (managed_inner or {}).items()}
    out = [p for p in probes.no_block if inner.get(p)]
    bridge_missing = False
    if probes.bridge is not None and managed_inner is not None and probes.bridge in inner:
        declared = inner[probes.bridge]
        if not declared:
            bridge_missing = True
        elif declared != BRIDGE_HASH:
            out.append(probes.bridge)
    for d in probes.skill_dirs:
        if local_hashes is not None and any(p.startswith(d + "/") for p in local_hashes):
            out.append(d + "/")
    return sorted(out), bridge_missing


def normalize_hash(value: str | None) -> str | None:
    """申告のハッシュ（`sha256:` 無しも受ける）。空・None は「区画が無い」。"""
    if not value:
        return None
    return value if value.startswith("sha256:") else "sha256:" + value.lower()


STALE_COPY_MESSAGE = "知識の古い写しが別の場所に残っています"
# 取り込み直しの案内（ゲートと sync_status で共有）
_PULL_AGAIN = "MCP の `knowledge_pull`（CLI なら `speclino sync`）で取り込み直"
BRIDGE_MISSING_MESSAGE = (
    "Claude Code が指示書を読めない: CLAUDE.md に @AGENTS.md の橋が無い。取り込み直す"
)


@dataclass(frozen=True)
class LockAssessment:
    """lock の鮮度の判定（ゲートの Finding と MCP の `sync_status` が同じものを使う）。"""

    verdict: Verdict
    message: str
    expected: str | None = None
    actual: str | None = None
    lock: KnowledgeLock | None = None
    changed: list[dict[str, Any]] = field(default_factory=list)
    # 取り込みが要る理由（MCP の `sync_status`、A8）。VERIFIED_IN_SCOPE なら空
    reasons: list[str] = field(default_factory=list)


def _change_reasons(changed: list[dict[str, Any]]) -> list[str]:
    kinds = {c["kind"] for c in changed}
    out = ["members_changed"] if kinds - {"policy"} else []
    return out + (["policy_changed"] if "policy" in kinds else [])


def copies_assessment(
    lock: KnowledgeLock, changed: list[dict[str, Any]], stale: list[str], bridge_missing: bool
) -> LockAssessment:
    """古い写し・橋の欠けの REVIEW（ゲートと `sync_status` で同じ文言）。"""
    messages, reasons = [], _change_reasons(changed)
    if stale:
        listed = ", ".join(stale[:20])
        messages.append(f"{STALE_COPY_MESSAGE}: {listed}。{_PULL_AGAIN}すか、手で消してください")
        reasons.append("stale_copy")
    if bridge_missing:
        messages.append(BRIDGE_MISSING_MESSAGE)
        reasons.append("bridge_missing")
    return LockAssessment(
        "REVIEW",
        "。".join(messages),
        expected="lock が示す配置のほかに、管理区画やスキルの写しが無い"
        + ("。CLAUDE.md があれば @AGENTS.md の橋がある" if bridge_missing else ""),
        actual=", ".join([*stale[:20], *(["CLAUDE.md（橋なし）"] if bridge_missing else [])]),
        lock=lock,
        changed=changed,
        reasons=reasons,
    )


def assess_lock(
    lock_json: str | None,
    *,
    current: Bundle,
    current_members: list[BundleMember],
    github_repo_id: int,
    verify: Callable[[bytes, str, str | None], bool] = bundle_signing.verify,
    now: datetime | None = None,
    snapshot: Snapshot | None = None,
) -> LockAssessment:
    """lock を今の bundle と比べる。snapshot があれば書き出したファイルも照合する。

    - UNKNOWN: lock が無い・読めない・署名不一致・別のワークスペース/リポジトリ・期限切れ・lock の
      世代が今より新しい・ファイルの改変や欠落・消したはずのファイルや区画（`absent`）が残っている・
      同期したスキルに未登録のファイル・同じ世代なのに
      このプロジェクトのメンバーか規約と違う（他のプロジェクトの lock の持ち込み）
    - REVIEW: lock の世代が古く、このプロジェクトのメンバーか規約（`policy_hash`）が違う
    - VERIFIED_IN_SCOPE: それ以外

    `current_members` は current をこのプロジェクトに当てたメンバー（`resolve_for_project`）。
    """
    if lock_json is None:
        return LockAssessment(
            "UNKNOWN",
            f"{LOCK_PATH} が無いため、どの知識で作業したか確認できません",
            expected="`speclino sync` が書いた lock",
            actual="lock なし",
            reasons=["no_lock"],
        )
    try:
        lock = KnowledgeLock.model_validate_json(lock_json)
    except (ValidationError, ValueError) as e:
        return LockAssessment(
            "UNKNOWN", f"{LOCK_PATH} を読めません", actual=str(e)[:500], reasons=["invalid_lock"]
        )
    if not verify(lock_payload(lock), lock.signature, lock.key_id):
        return LockAssessment(
            "UNKNOWN", f"{LOCK_PATH} の署名が合いません", lock=lock, reasons=["invalid_lock"]
        )
    if lock.workspace != current.workspace or lock.github_repo_id != github_repo_id:
        return LockAssessment(
            "UNKNOWN",
            f"{LOCK_PATH} は別のワークスペースかリポジトリのものです",
            lock=lock,
            reasons=["other_repo"],
        )
    if (now or datetime.now(UTC)) >= lock.expires_at:
        return LockAssessment(
            "UNKNOWN",
            f"{LOCK_PATH} の期限が切れています。{_PULL_AGAIN}してください",
            actual=f"期限 {lock.expires_at.isoformat()}",
            lock=lock,
            reasons=["expired"],
        )
    if lock.bundle_generation > current.generation:
        return LockAssessment(
            "UNKNOWN",
            f"{LOCK_PATH} の世代が今の bundle より新しく、照合できません",
            expected=f"bundle {current.generation} 以下",
            actual=f"bundle {lock.bundle_generation}",
            lock=lock,
            reasons=["invalid_lock"],
        )
    stale, bridge_missing = _stale_copies(snapshot, lock) if snapshot is not None else ([], False)
    if snapshot is not None:
        changed_files = _changed_files(snapshot, lock)
        if changed_files:
            return LockAssessment(
                "UNKNOWN",
                "sync で書き出した知識が改変されているか欠けています",
                expected="lock のハッシュと一致",
                actual=", ".join(changed_files[:20]),
                lock=lock,
                reasons=["files_modified"],
            )
        leftover = _leftover_absent(snapshot, lock)
        if leftover:
            return LockAssessment(
                "UNKNOWN",
                "sync で消したはずの知識が残っています",
                expected="lock の absent のファイル・管理区画が無い",
                actual=", ".join(leftover[:20]),
                lock=lock,
                reasons=["files_modified"],
            )
        extra = _unlisted_skill_files(snapshot, lock)
        if extra:
            return LockAssessment(
                "UNKNOWN",
                "同期したスキルに未登録のファイルがあります",
                expected="lock に載ったファイルだけ",
                actual=", ".join(extra[:20]),
                lock=lock,
                reasons=["files_modified"],
            )
    changed = changed_members(lock.members, current_members)
    # 規約もこの bundle の一部（規約の有効化でも世代が進む）。lock に規約が無く今はある場合も変化
    if lock.policy_hash != current.policy_hash:
        changed.append(
            {
                "name": "team-policy",
                "kind": "policy",
                "lock_revision": lock.policy_revision,
                "current_revision": current.policy_revision,
            }
        )
    if changed and lock.bundle_generation == current.generation:
        return LockAssessment(
            "UNKNOWN",
            f"{LOCK_PATH} のメンバーか規約がこのプロジェクトの bundle と合いません"
            "（他のプロジェクトの lock か改変）",
            lock=lock,
            changed=changed,
            reasons=_change_reasons(changed),
        )
    if stale or bridge_missing:
        return copies_assessment(lock, changed, stale, bridge_missing)
    if changed:
        return LockAssessment(
            "REVIEW",
            f"古い知識で作業した可能性があります。{_PULL_AGAIN}してください",
            expected=f"bundle {current.generation}",
            actual=f"bundle {lock.bundle_generation}",
            lock=lock,
            changed=changed,
            reasons=_change_reasons(changed),
        )
    return LockAssessment(
        "VERIFIED_IN_SCOPE",
        f"lock は bundle {lock.bundle_generation} を宣言し、今の知識と一致します",
        lock=lock,
    )


def check_lock_snapshot(
    snapshot: Snapshot,
    *,
    current: Bundle,
    current_members: list[BundleMember],
    github_repo_id: int,
    verify: Callable[[bytes, str, str | None], bool] = bundle_signing.verify,
    now: datetime | None = None,
) -> Finding:
    """snapshot（PR の head など）の `.speclino/lock.json` の鮮度。`KNOWLEDGE-LOCK-001`（必須）。

    判定は `assess_lock`。lock は「この checkout が宣言している bundle」であって、利用の証明では
    ない。`current_members`・`github_repo_id` は省略できない（省くと他のプロジェクトの lock を
    通してしまう）。
    """
    try:
        lock_json = snapshot.read_text(LOCK_PATH)
    except (UnicodeDecodeError, OSError):
        lock_json = None
    a = assess_lock(
        lock_json,
        current=current,
        current_members=current_members,
        github_repo_id=github_repo_id,
        verify=verify,
        now=now,
        snapshot=snapshot,
    )
    return _finding(snapshot, a.verdict, a.message, expected=a.expected, actual=a.actual)


def check_lock(
    repo_root: Path,
    lock_json: str | None,
    *,
    current: Bundle,
    current_members: list[BundleMember],
    github_repo_id: int,
    verify: Callable[[bytes, str, str | None], bool] = bundle_signing.verify,
    now: datetime | None = None,
) -> Finding:
    """手元のディレクトリ版。lock の本文は呼び出し側が渡す（無ければ None）。"""
    snapshot = cast(Snapshot, DirectorySnapshot(repo_root))  # frozen dataclass の属性は読み取り専用
    a = assess_lock(
        lock_json,
        current=current,
        current_members=current_members,
        github_repo_id=github_repo_id,
        verify=verify,
        now=now,
        snapshot=snapshot,
    )
    return _finding(snapshot, a.verdict, a.message, expected=a.expected, actual=a.actual)


_APPLY_SCRIPT = Path(sync_apply.__file__).read_bytes()
_APPLY_SCRIPT_SHA256 = hashlib.sha256(_APPLY_SCRIPT).hexdigest()


def apply_script_sha256() -> str:
    """配る実行スクリプト（`speclino.sync_apply` のモジュールファイル）の sha256（素の hex）。"""
    return _APPLY_SCRIPT_SHA256


_SCRIPT_ID = re.compile(r"[0-9a-f]{64}")


def apply_script_id(token: str) -> str:
    """実行スクリプトの取得 URL に載せる ID（取得トークンのハッシュ）。URL がログに残っても、
    ハッシュからトークンは戻せないので計画を先取りされない（スクリプトの取得は消費しないため）。"""
    return _ticket_hash(token)


async def apply_script_for_id(
    session: AsyncSession, script_id: str, *, now: datetime | None = None
) -> bytes | None:
    """取得トークンが有効なら実行スクリプトのバイト列。計画の取得と同じ検証を、消費せずに行う
    （取得の前後どちらでも期限内なら取れる）。無効なら理由を区別せず None。"""
    if not _SCRIPT_ID.fullmatch(script_id):
        return None
    record = await SyncPlanRepository(session).peek(script_id, now=now or datetime.now(UTC))
    if record is None or not await _ticket_still_valid(session, record):
        return None
    return _APPLY_SCRIPT


__all__ = [
    "LOCK_PATH",
    "LOCK_REQUIREMENT_ID",
    "LOCK_TTL",
    "MANAGED_FILES",
    "SKILL_ROOTS",
    "SPEC_ROOT",
    "LockAssessment",
    "ManagedBlock",
    "ManagedBlockError",
    "PLAN_URL_TTL",
    "PlanTicket",
    "PlanTooLarge",
    "SyncRejected",
    "DEFAULT_LAYOUT",
    "STALE_COPY_MESSAGE",
    "apply_script_for_id",
    "apply_script_id",
    "apply_script_sha256",
    "assess_lock",
    "build_plan",
    "changed_members",
    "check_lock",
    "check_lock_snapshot",
    "fetch_for_sync",
    "issue_plan_ticket",
    "redeem_plan_ticket",
    "file_hash",
    "instructions_files",
    "layout_changes",
    "is_valid_agent",
    "lock_skill_roots",
    "normalize_agent",
    "normalize_hash",
    "agent_skills_root",
    "declared_copies",
    "copies_assessment",
    "CopyProbes",
    "BRIDGE_INNER",
    "issue_lock",
    "lock_payload",
    "merge_managed",
    "plan_for_project",
    "plan_json_bytes",
    "plan_payload",
    "plan_writes",
    "project_members",
    "remove_managed",
    "removed_paths",
    "render_files",
    "resolve_layout",
    "stale_copy_probes",
    "split_managed",
    "validate_rel_path",
    "verified_previous_lock",
    "verify_lock",
    "verify_plan",
]
