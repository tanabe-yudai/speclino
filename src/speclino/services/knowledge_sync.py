"""知識の配信（S68、design/2026-10-05-team-knowledge-registry.md 7.1・8 章）。

3 つの面を持つ:

- サーバ: 対象のプロジェクトに当たる bundle の内容を、書き出すファイルに組み立て（`render_files`）、
  署名つきの lock を作る（`issue_lock`）。`fetch_for_sync` が sync トークンの要求に答え、
  `knowledge_fetches` に記録する
- クライアント（`speclino sync`）: `write_bundle` が受け取ったファイルを検証してから、一時
  ディレクトリに書き、全部そろってから置き換える。lock は最後に書く。失敗したら前の状態と
  lock を保つ
- ゲート: `check_lock_snapshot` / `check_lock` が lock の署名・期限・ファイルのハッシュ・世代を
  確かめ、`KNOWLEDGE-LOCK-001`（必須）の Finding を 1 つ返す（run への配線は S65）

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

import contextlib
import os
import posixpath
import re
import secrets
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project
from speclino.db.repositories.sync_tokens import KnowledgeFetchRepository
from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Severity, Verdict
from speclino.domain.knowledge import (
    MANAGED_BEGIN,
    MANAGED_END,
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
from speclino.domain.knowledge_sync import SyncIdentity, SyncPayload
from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr
from speclino.spec.loader import content_hash
from speclino.verifiers.base import Snapshot
from speclino.verifiers.snapshot import DirectorySnapshot

LOCK_PATH = ".speclino/lock.json"
LOCK_REQUIREMENT_ID = "KNOWLEDGE-LOCK-001"
LOCK_TTL = timedelta(days=30)
MANAGED_FILES: dict[str, InstructionsTarget] = {"AGENTS.md": "agents_md", "CLAUDE.md": "claude_md"}
SKILL_ROOTS = (".claude/skills", ".agents/skills")
SPEC_ROOT = ".speclino/specs"

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MARKER_ANY = re.compile(r"<!--\s*speclino\s*:\s*(begin|end)", re.IGNORECASE)
_BEGIN_LINE = re.compile(r"<!-- speclino:begin bundle=(\d+) -->\n")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_TMP_PREFIX = ".tmp-"


class SyncRejected(ValueError):
    """書き出しを拒否した（パス・symlink・管理区画の不正など）。手元の状態と lock は前のまま。"""


class ManagedBlockError(ValueError):
    """管理区画のマーカーが壊れている・重複している（その手元のファイルには触らない）。"""


# ---------------------------------------------------------------------------
# 共通: ハッシュと管理区画
# ---------------------------------------------------------------------------


def file_hash(text: str) -> str:
    """lock に書くハッシュ。改行を LF に寄せてから sha256（autocrlf の checkout でも一致する）。"""
    return content_hash(text.replace("\r\n", "\n").replace("\r", "\n"))


def lock_payload(lock: KnowledgeLock) -> bytes:
    """lock の署名の対象（`signature` 以外の正規 JSON）。"""
    return kr.canonical_json(lock.model_dump(mode="json", exclude={"signature"}))


def split_managed(text: str) -> tuple[str, str, str] | None:
    """(区画の前, 区画の中身, 区画の後)。区画が無ければ None、壊れていれば `ManagedBlockError`。

    begin / end がちょうど 1 つずつ、この順で、begin が正規の 1 行であるときだけ受け付ける。
    """
    markers = list(_MARKER_ANY.finditer(text))
    if not markers:
        return None
    kinds = [m.group(1).lower() for m in markers]
    if kinds != ["begin", "end"]:
        raise ManagedBlockError("管理区画のマーカーが重複しているか順序が違う")
    begin = _BEGIN_LINE.match(text, markers[0].start())
    end_at = markers[1].start()
    if begin is None or not text.startswith(MANAGED_END, end_at):
        raise ManagedBlockError("管理区画のマーカーの形が正しくない")
    return text[: markers[0].start()], text[begin.end() : end_at], text[end_at + len(MANAGED_END) :]


def _block(inner: str, generation: int) -> str:
    return f"{MANAGED_BEGIN} bundle={generation} -->\n{inner}{MANAGED_END}"


def merge_managed(existing: str | None, inner: str, generation: int) -> str:
    """existing の管理区画を inner に置き換えた本文。区画が無ければ末尾に足す。"""
    if _MARKER_ANY.search(inner):
        raise ManagedBlockError("区画の中身に管理区画のマーカーがある")
    block = _block(inner, generation)
    if existing is None or existing == "":
        return block + "\n"
    parts = split_managed(existing)
    if parts is None:
        sep = "\n" if existing.endswith("\n") else "\n\n"
        return existing + sep + block + "\n"
    before, _, after = parts
    return before + block + after


def remove_managed(existing: str) -> str:
    """管理区画を取り除いた本文（区画が無ければそのまま）。"""
    parts = split_managed(existing)
    if parts is None:
        return existing
    before, _, after = parts
    return before + (after[1:] if after.startswith("\n") else after)


# ---------------------------------------------------------------------------
# サーバ: 書き出すファイルと lock
# ---------------------------------------------------------------------------


def _skill_dir(rev: ArtifactRevision) -> str:
    # 書き出し先の名前は bundle の競合検査と同じ（frontmatter の name、無ければ artifact 名）
    name = kr._skill_name(rev)
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


def _members(resolved: ResolvedKnowledge) -> list[BundleMember]:
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
    now: datetime | None = None,
) -> KnowledgeLock:
    """署名つきの lock。`files` は `render_files` の結果（ハッシュは書き出した中身のもの）。"""
    issued = now or datetime.now(UTC)
    unsigned = KnowledgeLock(
        workspace=resolved.bundle.workspace,
        github_repo_id=github_repo_id,
        bundle_generation=resolved.bundle.generation,
        members=_members(resolved),
        files={p: file_hash(body) for p, body in sorted(files.items())},
        issued_at=issued,
        expires_at=issued + LOCK_TTL,
        signature="unsigned",
    )
    return unsigned.model_copy(update={"signature": bundle_signing.sign(lock_payload(unsigned))})


async def fetch_for_sync(
    session: AsyncSession, identity: SyncIdentity, github_repo_id: int
) -> SyncPayload:
    """sync トークンの要求に答える。トークンのプロジェクトのリポジトリでなければ `LookupError`。

    今の bundle（無ければ作る）をプロジェクトに当て、書き出すファイルと lock を作り、取得を
    `knowledge_fetches` に記録する。
    """
    project = await session.get(Project, identity.project_id)
    if (
        project is None
        or project.team != identity.workspace
        or project.github_repo_id != github_repo_id
    ):
        raise LookupError("このトークンのリポジトリではない")
    bundle = await kr.ensure_bundle(session, identity.workspace)
    resolved = await kr.resolve_for_project(session, bundle, str(project.id))
    files = render_files(resolved)
    lock = issue_lock(resolved, github_repo_id=github_repo_id, files=files)
    await KnowledgeFetchRepository(session).record(
        workspace=identity.workspace,
        channel="sync",
        token_id=identity.token_id,
        bundle_generation=bundle.generation,
        project_id=project.id,
        artifact_ids=[m.artifact_id for m in lock.members],
    )
    return SyncPayload(bundle=bundle, files=files, lock=lock)


# ---------------------------------------------------------------------------
# クライアント: 書き出し
# ---------------------------------------------------------------------------


def validate_rel_path(path: str) -> None:
    """書き出してよい相対パスか。ルート外・絶対パス・`..`・知らない置き場は `SyncRejected`。"""
    norm = posixpath.normpath(path) if path else ""
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or path.startswith("/")
        or _DRIVE_LETTER.match(path)
        or norm != path
        or any(seg in ("", ".", "..") for seg in path.split("/"))
    ):
        raise SyncRejected(f"書き出し先のパスが不正: {path!r}")
    parts = path.split("/")
    if path in MANAGED_FILES:
        return
    for root in SKILL_ROOTS:
        root_parts = root.split("/")
        if (
            parts[: len(root_parts)] == root_parts
            and len(parts) >= len(root_parts) + 2
            and _NAME.match(parts[len(root_parts)])
        ):
            return
    if (
        len(parts) == 3
        and "/".join(parts[:2]) == SPEC_ROOT
        and parts[2].endswith(".md")
        and _NAME.match(parts[2][: -len(".md")])
    ):
        return
    raise SyncRejected(f"書き出し先が SpecLino の置き場ではない: {path!r}")


def _check_target(root: Path, path: str) -> Path:
    """root/path の途中に symlink が無く、途中はディレクトリ、終端はファイルか無いこと。"""
    current = root
    parts = path.split("/")
    for i, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise SyncRejected(f"書き出し先の途中に symlink がある: {path}")
        last = i == len(parts) - 1
        if current.exists():
            if last and current.is_dir():
                raise SyncRejected(f"書き出し先がディレクトリになっている: {path}")
            if not last and not current.is_dir():
                raise SyncRejected(f"書き出し先の途中がファイルになっている: {path}")
    if not current.resolve().is_relative_to(root):
        raise SyncRejected(f"書き出し先がリポジトリの外: {path}")
    return current


def _read_text(target: Path) -> str | None:
    if not target.exists():
        return None
    try:
        return target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise SyncRejected(f"{target.name} を UTF-8 として読めない") from e


def _previous_lock(root: Path) -> KnowledgeLock | None:
    """手元の lock（読めなければ None。削除の根拠には使えないので何も消さない）。"""
    target = root / LOCK_PATH
    if target.is_symlink() or not target.is_file():
        return None
    try:
        return KnowledgeLock.model_validate_json(target.read_bytes())
    except (ValidationError, ValueError):
        return None


@dataclass
class _Op:
    """1 つの置き換え。`staged` が None なら削除。"""

    target: Path
    staged: Path | None


def _plan_removals(root: Path, previous: KnowledgeLock | None, new_paths: set[str]) -> list[str]:
    """前の lock に載っていて今回は無いファイル（skill・spec）。lock に無いものは消さない。"""
    if previous is None:
        return []
    out = []
    for path in sorted(set(previous.files) - new_paths):
        if path in MANAGED_FILES:
            continue
        try:
            validate_rel_path(path)
            target = _check_target(root, path)
        except SyncRejected:
            continue  # 不正なパスの記録は無視する（消す根拠にしない）
        if target.is_file():
            out.append(path)
    return out


def write_bundle(repo_root: Path, files: dict[str, str], lock: KnowledgeLock) -> None:
    """files を repo_root に書き出し、最後に `.speclino/lock.json` を書く。

    1. すべてのパスと、files と lock の対応（パスの集合・ハッシュ）を検証する
    2. `.speclino/.tmp-<乱数>/` に新しい中身を全部書く（管理区画は手元のファイルに差し込む）
    3. 全部そろってから、各ファイルを `os.replace` で置き換える（前の中身は一時ディレクトリに
       退避）。途中で失敗したら退避から戻す
    4. 前の lock にあって今回無い skill / spec のファイルを消し、空になったスキルのディレクトリを
       消す。前回の区画があって今回の指示書が無ければ区画を取り除く
    5. lock を置き換える

    どこで失敗しても `SyncRejected`（または OSError）で、手元のファイルと lock は前のまま。
    """
    root = repo_root.resolve(strict=True)
    if set(files) != set(lock.files):
        raise SyncRejected("files と lock のパスが一致しない")
    for path, body in files.items():
        validate_rel_path(path)
        if file_hash(body) != lock.files[path]:
            raise SyncRejected(f"{path} の中身が lock のハッシュと一致しない")
        if path in MANAGED_FILES and _MARKER_ANY.search(body):
            raise SyncRejected(f"{path} の区画の中身に管理区画のマーカーがある")
    lock_target = _check_target(root, LOCK_PATH)
    targets = {path: _check_target(root, path) for path in files}

    previous = _previous_lock(root)
    removals = _plan_removals(root, previous, set(files))
    managed_out = [
        p for p in MANAGED_FILES if p not in files and previous is not None and p in previous.files
    ]

    contents: dict[str, str] = {}
    try:
        for path, body in files.items():
            if path in MANAGED_FILES:
                contents[path] = merge_managed(
                    _read_text(targets[path]), body, lock.bundle_generation
                )
            else:
                contents[path] = body
        for path in managed_out:
            target = _check_target(root, path)
            existing = _read_text(target)
            if existing is not None:
                targets[path] = target
                contents[path] = remove_managed(existing)
    except ManagedBlockError as e:
        raise SyncRejected(f"管理区画が壊れているため書き出さない: {e}") from e

    (root / ".speclino").mkdir(exist_ok=True)
    tmp = root / ".speclino" / f"{_TMP_PREFIX}{secrets.token_hex(8)}"
    tmp.mkdir()
    try:
        ops = _stage(tmp, targets, contents)
        ops += [_Op(_check_target(root, p), None) for p in removals]
        lock_staged = tmp / "lock.json"
        lock_staged.write_bytes(lock.model_dump_json(indent=2).encode() + b"\n")
        ops.append(_Op(lock_target, lock_staged))
        _commit(root, tmp, ops)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _stage(tmp: Path, targets: dict[str, Path], contents: dict[str, str]) -> list[_Op]:
    ops = []
    for i, (path, text) in enumerate(sorted(contents.items())):
        staged = tmp / f"f{i}"
        staged.write_bytes(text.encode("utf-8"))
        ops.append(_Op(targets[path], staged))
    return ops


def _commit(root: Path, tmp: Path, ops: list[_Op]) -> None:
    """ops を順に適用する。途中で失敗したら、適用済みのものを退避から戻して例外を投げ直す。"""
    done: list[tuple[_Op, Path | None]] = []
    created_dirs: list[Path] = []
    try:
        for i, op in enumerate(ops):
            backup: Path | None = None
            if op.target.exists():
                backup = tmp / f"b{i}"
                os.replace(op.target, backup)
            done.append((op, backup))
            if op.staged is not None:
                created_dirs += _make_parents(root, op.target.parent)
                os.replace(op.staged, op.target)
    except BaseException:
        for op, backup in reversed(done):
            if backup is not None:
                os.replace(backup, op.target)
            elif op.staged is not None and op.target.exists():
                op.target.unlink()
        for d in reversed(created_dirs):
            _rmdir_if_empty(d)
        raise
    for op, _ in done:
        if op.staged is None:
            _prune_empty_parents(root, op.target.parent)


def _make_parents(root: Path, directory: Path) -> list[Path]:
    created = []
    for d in reversed([directory, *directory.parents]):
        if d == root or not d.is_relative_to(root) or d.exists():
            continue
        d.mkdir()
        created.append(d)
    return created


def _rmdir_if_empty(d: Path) -> None:
    with contextlib.suppress(OSError):
        d.rmdir()


def _prune_empty_parents(root: Path, directory: Path) -> None:
    """消したファイルの親で、空になったスキル・仕様のディレクトリを消す（置き場の根は残す）。"""
    keep = {root / r for r in SKILL_ROOTS} | {root / SPEC_ROOT}
    d = directory
    while d != root and d not in keep and d.is_relative_to(root):
        if d.is_dir():  # 先に別のファイルの片付けで消えていることがある
            if any(d.iterdir()):
                return
            d.rmdir()
        d = d.parent


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


def _changed_files(snapshot: Snapshot, lock: KnowledgeLock) -> list[str]:
    changed = []
    for path, expected in sorted(lock.files.items()):
        try:
            text = snapshot.read_text(path)
            if text is not None and path in MANAGED_FILES:
                parts = split_managed(text)
                text = parts[1] if parts is not None else None
        except (UnicodeDecodeError, ManagedBlockError, OSError):
            text = None
        if text is None or file_hash(text) != expected:
            changed.append(path)
    return changed


def _evaluate(
    snapshot: Snapshot,
    lock_json: str | None,
    *,
    current: Bundle,
    verify: Callable[[bytes, str], bool],
    current_members: list[BundleMember] | None,
    github_repo_id: int | None,
    now: datetime | None,
) -> Finding:
    if lock_json is None:
        return _finding(
            snapshot,
            "UNKNOWN",
            f"{LOCK_PATH} が無いため、どの知識で作業したか確認できません",
            expected="`speclino sync` が書いた lock",
            actual="lock なし",
        )
    try:
        lock = KnowledgeLock.model_validate_json(lock_json)
    except (ValidationError, ValueError) as e:
        return _finding(snapshot, "UNKNOWN", f"{LOCK_PATH} を読めません", actual=str(e)[:500])
    if not verify(lock_payload(lock), lock.signature):
        return _finding(snapshot, "UNKNOWN", f"{LOCK_PATH} の署名が合いません")
    if lock.workspace != current.workspace or (
        github_repo_id is not None and lock.github_repo_id != github_repo_id
    ):
        return _finding(
            snapshot, "UNKNOWN", f"{LOCK_PATH} は別のワークスペースかリポジトリのものです"
        )
    if (now or datetime.now(UTC)) >= lock.expires_at:
        return _finding(
            snapshot,
            "UNKNOWN",
            f"{LOCK_PATH} の期限が切れています。`speclino sync` をやり直してください",
            actual=f"期限 {lock.expires_at.isoformat()}",
        )
    if lock.bundle_generation > current.generation:
        return _finding(
            snapshot,
            "UNKNOWN",
            f"{LOCK_PATH} の世代が今の bundle より新しく、照合できません",
            expected=f"bundle {current.generation} 以下",
            actual=f"bundle {lock.bundle_generation}",
        )
    changed = _changed_files(snapshot, lock)
    if changed:
        return _finding(
            snapshot,
            "UNKNOWN",
            "sync で書き出した知識が改変されているか欠けています",
            expected="lock のハッシュと一致",
            actual=", ".join(changed[:20]),
        )
    if lock.bundle_generation < current.generation and (
        current_members is None or _member_keys(current_members) != _member_keys(lock.members)
    ):
        return _finding(
            snapshot,
            "REVIEW",
            "古い知識で作業した可能性があります。`speclino sync` で最新にしてください",
            expected=f"bundle {current.generation}",
            actual=f"bundle {lock.bundle_generation}",
        )
    return _finding(
        snapshot,
        "VERIFIED_IN_SCOPE",
        f"lock は bundle {lock.bundle_generation} を宣言し、書き出したファイルと一致します",
    )


def check_lock_snapshot(
    snapshot: Snapshot,
    *,
    current: Bundle,
    verify: Callable[[bytes, str], bool] = bundle_signing.verify,
    current_members: list[BundleMember] | None = None,
    github_repo_id: int | None = None,
    now: datetime | None = None,
) -> Finding:
    """snapshot（PR の head など）の `.speclino/lock.json` の鮮度。`KNOWLEDGE-LOCK-001`（必須）。

    - lock が無い・読めない・署名不一致・期限切れ・別のワークスペース/リポジトリ・書き出した
      ファイルの改変や欠落 → UNKNOWN
    - lock の世代が current より古く、このプロジェクトのメンバーが違う → REVIEW。`current_members`
      （current をプロジェクトに当てたメンバー）を渡さなければ、世代が古いだけで REVIEW（安全側）
    - それ以外 → VERIFIED_IN_SCOPE

    lock は「この checkout が宣言している bundle」であって、利用の証明ではない。
    """
    try:
        lock_json = snapshot.read_text(LOCK_PATH)
    except (UnicodeDecodeError, OSError):
        lock_json = None
    return _evaluate(
        snapshot,
        lock_json,
        current=current,
        verify=verify,
        current_members=current_members,
        github_repo_id=github_repo_id,
        now=now,
    )


def check_lock(
    repo_root: Path,
    lock_json: str | None,
    *,
    current: Bundle,
    verify: Callable[[bytes, str], bool] = bundle_signing.verify,
    current_members: list[BundleMember] | None = None,
    github_repo_id: int | None = None,
    now: datetime | None = None,
) -> Finding:
    """手元のディレクトリ版。lock の本文は呼び出し側が渡す（無ければ None）。"""
    return _evaluate(
        cast(Snapshot, DirectorySnapshot(repo_root)),  # frozen dataclass の属性は読み取り専用
        lock_json,
        current=current,
        verify=verify,
        current_members=current_members,
        github_repo_id=github_repo_id,
        now=now,
    )


__all__ = [
    "LOCK_PATH",
    "LOCK_REQUIREMENT_ID",
    "LOCK_TTL",
    "MANAGED_FILES",
    "SKILL_ROOTS",
    "SPEC_ROOT",
    "ManagedBlockError",
    "SyncRejected",
    "check_lock",
    "check_lock_snapshot",
    "fetch_for_sync",
    "file_hash",
    "issue_lock",
    "lock_payload",
    "merge_managed",
    "remove_managed",
    "render_files",
    "split_managed",
    "validate_rel_path",
    "write_bundle",
]
