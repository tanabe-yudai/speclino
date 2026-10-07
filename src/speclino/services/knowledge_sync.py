"""知識の配信（S68・S75、design/2026-10-05-team-knowledge-registry.md 7.1・8 章、
design/2026-10-07-agent-sync.md）。

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

書き出しの約束:

- skill: `.claude/skills/<name>/` と `.agents/skills/<name>/` に `SKILL.md` と付属ファイル
- instructions: `AGENTS.md` / `CLAUDE.md` の管理区画。workspace スコープ → プロジェクトスコープ、
  それぞれ名前順に連結
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
    InstructionsTarget,
    KnowledgeLock,
    ResolvedKnowledge,
    SkillContent,
    SpecContent,
)
from speclino.domain.knowledge_sync import (
    DeleteOp,
    RemoveManagedBlockOp,
    SetManagedBlockOp,
    SyncIdentity,
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
LOCK_TTL = timedelta(days=30)
MANAGED_FILES: dict[str, InstructionsTarget] = {"AGENTS.md": "agents_md", "CLAUDE.md": "claude_md"}

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 書き出しを拒否した（パス・symlink・管理区画の不正・上限など）。手元の状態と lock は前のまま
SyncRejected = sync_apply.PreconditionFailed


def plan_json_bytes(plan: SyncPlan) -> int:
    """計画の JSON の大きさ（MCP の 1 回の応答の上限の判定に使う）。"""
    return len(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))


# 後から足した欄。None・空のときは署名の対象に含めない（欄の無い古い lock の署名を保つ）
_OPTIONAL_LOCK_FIELDS = ("policy_revision", "policy_hash", "key_id", "absent")


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


def render_files(resolved: ResolvedKnowledge) -> dict[str, str]:
    """プロジェクトに当たる知識から、書き出すファイル（相対パス → 本文）を作る。決定的な順序。"""
    files: dict[str, str] = {}
    for rev in sorted(resolved.skills, key=lambda r: r.name):
        assert isinstance(rev.content, SkillContent)
        name = _skill_dir(rev)
        for root in SKILL_ROOTS:
            files[f"{root}/{name}/SKILL.md"] = rev.content.skill_md
            for rel, body in sorted(rev.content.files.items()):
                files[f"{root}/{name}/{rel}"] = body
    for path, target in MANAGED_FILES.items():
        bodies = [
            _with_newline(r.content.body)
            for r in sorted(
                resolved.instructions, key=lambda r: (not r.content.scope.workspace, r.name)
            )
            if isinstance(r.content, InstructionsContent) and r.content.target == target
        ]
        if bodies:
            files[path] = "\n".join(bodies)
    for rev in sorted(resolved.specs, key=lambda r: r.name):
        assert isinstance(rev.content, SpecContent)
        files[f"{SPEC_ROOT}/{rev.name}.md"] = rev.content.markdown
    for path in files:
        validate_rel_path(path)
    return files


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
    now: datetime | None = None,
) -> KnowledgeLock:
    """署名つきの lock。`files` は `render_files` の結果（ハッシュは書き出した中身のもの）。

    `absent` は前回あって今回は無いパス（`removed_paths`）。ゲートが残っていないことを照合する。
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
) -> SyncPlan:
    """書き出しの計画を作って署名する（純粋）。`previous` は `verified_previous_lock` の結果だけ。

    - `files` の各パスは `write`（AGENTS.md / CLAUDE.md は `set_managed_block`）。前回の lock の
      記録があれば `expected_*` に入れる（実行スクリプトは記録どおりの手元のファイルだけ上書きする）
    - `lock.absent` の各パスは `delete`（AGENTS.md / CLAUDE.md は `remove_managed_block`）。根拠は
      前回の lock の記録だけ（`previous` が無ければ `absent` は空でなければならない）
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
            ops.append(
                SetManagedBlockOp(
                    path=path,
                    inner=files[path],
                    generation=lock.bundle_generation,
                    expected_inner_sha256=before.get(path),
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
    now: datetime | None = None,
) -> SyncPlan:
    """プロジェクトに当たる知識から、書き出すファイル・lock（`absent` つき）・計画を作る。"""
    files = render_files(resolved)
    lock = issue_lock(
        resolved,
        github_repo_id=github_repo_id,
        files=files,
        absent=removed_paths(files, previous),
        now=now,
    )
    return plan_writes(files, lock, previous, repo_full_name=repo_full_name)


class PlanTooLarge(ValueError):
    """計画が上限（`sync_apply.MAX_PLAN_BYTES`、CLI・MCP 共通）を超えた。"""


PLAN_URL_TTL = timedelta(minutes=10)
PLAN_TOKEN_PREFIX = "slplan_"


async def _plan_for(
    session: AsyncSession, *, workspace: str, project: Project, lock_json: str | None
) -> SyncPlan:
    if project.github_repo_id is None:
        raise ValueError("リポジトリ ID が未登録のため書き出せない")
    bundle = await kr.ensure_bundle(session, workspace)
    resolved = await kr.resolve_for_project(session, bundle, str(project.id))
    previous = verified_previous_lock(
        lock_json, workspace=workspace, github_repo_id=project.github_repo_id
    )
    plan = build_plan(
        resolved,
        github_repo_id=project.github_repo_id,
        repo_full_name=project.repo_full_name,
        previous=previous,
    )
    size = plan_json_bytes(plan)
    if size > sync_apply.MAX_PLAN_BYTES:
        raise PlanTooLarge(f"計画が {sync_apply.MAX_PLAN_BYTES} バイトを超える（{size} バイト）")
    return plan


async def plan_for_project(
    session: AsyncSession,
    *,
    workspace: str,
    project: Project,
    lock_json: str | None,
    channel: str,
    token_id: uuid.UUID | None,
) -> SyncPlan:
    """project（呼び出し側がテナントを確かめたもの）に当たる今の bundle の計画を作り、記録する。

    lock のリポジトリ ID は要求の値ではなく project のものを使う。上限を超えれば
    `PlanTooLarge`（記録しない）。bundle を読めなければ ValueError / LookupError。
    """
    plan = await _plan_for(session, workspace=workspace, project=project, lock_json=lock_json)
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


async def issue_plan_ticket(
    session: AsyncSession,
    *,
    workspace: str,
    project: Project,
    lock_json: str | None,
    channel: str,
    token_id: uuid.UUID | None,
    now: datetime | None = None,
) -> PlanTicket:
    """計画を作って `sync_plans` に置き、1 回だけ・10 分で失効する取得トークンを返す。

    保存するのはトークンの SHA-256 だけ。取得の記録（`knowledge_fetches`）は実際に取得したとき
    （`redeem_plan_ticket`）に残す。
    """
    plan = await _plan_for(session, workspace=workspace, project=project, lock_json=lock_json)
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
    removes = {"delete", "remove_managed_block"}
    return PlanTicket(
        token=token,
        expires_at=issued + PLAN_URL_TTL,
        bundle_generation=plan.bundle_generation,
        ops=len(plan.ops),
        writes=sum(op.op not in removes for op in plan.ops),
        deletes=sum(op.op in removes for op in plan.ops),
        bytes=len(body.encode("utf-8")),
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
    project = await session.get(Project, record.project_id)
    if (
        project is None
        or project.team != record.workspace
        or project.github_repo_id != record.github_repo_id
        or not await _issuer_still_valid(session, record)
    ):
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
            f"{LOCK_PATH} の期限が切れています。`speclino sync` をやり直してください",
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
    if changed:
        return LockAssessment(
            "REVIEW",
            "古い知識で作業した可能性があります。`speclino sync` で最新にしてください",
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
    "assess_lock",
    "build_plan",
    "changed_members",
    "check_lock",
    "check_lock_snapshot",
    "fetch_for_sync",
    "issue_plan_ticket",
    "redeem_plan_ticket",
    "file_hash",
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
    "split_managed",
    "validate_rel_path",
    "verified_previous_lock",
    "verify_lock",
    "verify_plan",
]
