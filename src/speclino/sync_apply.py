"""SpecLino の知識の書き出し計画（SyncPlan）を checkout に当てる実行スクリプト（S75）。

design/2026-10-07-agent-sync.md の A2–A6・3 章。標準ライブラリだけで書き、Python 3.10 以上で
動く（speclino を入れていない環境でも、プラグインの `scripts/speclino_apply.py` として走る）。
正本は `src/speclino/sync_apply.py` で、プラグインの写しとのバイト一致はテストで固定する。

    python3 speclino_apply.py <plan.json | -> [--repo DIR]

- 計画はサーバが作って署名する。ここでは署名を確かめない（鍵を持たない）。かわりに、計画と lock の
  リポジトリ ID の一致と、`.git` の remote から `owner/name` を推定できればその一致を確かめる
- すべての操作の前提（許可リストのパス・パスの途中に symlink が無い・ディレクトリの取り違えが無い・
  `expected_*` の一致・管理区画の印が 1 組だけ・上限・lock と操作の対応）を先に全部確かめ、1 つでも
  崩れたら何も書かずに終了コード 2
- 通れば `.speclino/.tmp-<乱数>/` に新しい中身を全部書き、置き換え直前に symlink を確かめ直してから
  1 つずつ置き換える。途中で失敗したら退避から元に戻して終了コード 1。lock は最後に書く
- 結果は JSON 1 行（書いた・消した・変えなかったパスと、止まった理由）

op の種類:

- `write{path, content, sha256, expected_sha256?}`: ファイル全体。既存のファイルの中身が新しい中身と
  違えば、`expected_sha256`（前回の lock の記録）と一致するときだけ上書きする
- `set_managed_block{path, inner, generation, expected_inner_sha256?}`: AGENTS.md / CLAUDE.md の
  管理区画の中だけを置き換える（無ければ末尾に足す）。区画の外は触らない
- `remove_managed_block{path, expected_inner_sha256}`: 区画だけを取り除く
- `delete{path, expected_sha256}`: 中身が記録どおりのときだけ消す

ハッシュは改行を LF に寄せた本文の `sha256:<hex>`（Windows の autocrlf で偽の改変にしない）。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

LOCK_PATH = ".speclino/lock.json"
MANAGED_FILES = ("AGENTS.md", "CLAUDE.md")
SKILL_ROOTS = (".claude/skills", ".agents/skills")
SPEC_ROOT = ".speclino/specs"
MANAGED_BEGIN = "<!-- speclino:begin"
MANAGED_END = "<!-- speclino:end -->"

# 書き出しの上限（計画の作成時と同じ値。壊れた計画でも手元を埋め尽くさない）
MAX_FILE_BYTES = 256 * 1024
MAX_MANAGED_BYTES = 4 * MAX_FILE_BYTES  # 複数の指示書を連結するので 1 本より大きくなりうる
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 2000

EXIT_OK = 0
EXIT_FAILED = 1  # 予期しない失敗（元に戻した）
EXIT_PRECONDITION = 2  # 前提が崩れた（何も書いていない）

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MARKER_ANY = re.compile(r"<!--\s*speclino\s*:\s*(begin|end)", re.IGNORECASE)
_BEGIN_LINE = re.compile(r"<!-- speclino:begin bundle=(\d+) -->\r?\n")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_GITHUB_REMOTE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https?://(?:[^@/]+@)?github\.com/)"
    r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_TMP_PREFIX = ".tmp-"
_OPS = ("write", "set_managed_block", "remove_managed_block", "delete")


class PreconditionFailed(ValueError):
    """計画を当てる前提が崩れた（何も書いていない）。"""


class ManagedBlockError(ValueError):
    """管理区画の印が壊れている・重複している（そのファイルには触らない）。"""


# ---------------------------------------------------------------------------
# ハッシュ・パス・管理区画（サーバの knowledge_sync も同じ関数を使う）
# ---------------------------------------------------------------------------


def file_hash(text: str) -> str:
    """lock に書くハッシュ。改行を LF に寄せてから sha256。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_rel_path(path: str) -> None:
    """書き出してよい相対パスか。ルート外・絶対パス・`..`・知らない置き場は拒否する。"""
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
        raise PreconditionFailed(f"書き出し先のパスが不正: {path!r}")
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
    raise PreconditionFailed(f"書き出し先のパスが SpecLino の置き場ではない: {path!r}")


def check_target(root: Path, path: str) -> Path:
    """root/path の途中に symlink が無く、途中はディレクトリ、終端はファイルか無いこと。"""
    current = root
    parts = path.split("/")
    for i, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise PreconditionFailed(f"書き出し先の途中に symlink がある: {path}")
        last = i == len(parts) - 1
        if current.exists():
            if last and current.is_dir():
                raise PreconditionFailed(f"書き出し先がディレクトリになっている: {path}")
            if not last and not current.is_dir():
                raise PreconditionFailed(f"書き出し先の途中がファイルになっている: {path}")
    resolved = current.resolve()
    if resolved != root and root not in resolved.parents:
        raise PreconditionFailed(f"書き出し先がリポジトリの外: {path}")
    return current


class ManagedBlock(NamedTuple):
    before: str
    inner: str
    after: str
    generation: int


def split_managed(text: str) -> ManagedBlock | None:
    """管理区画の前・中身・後と begin 行の世代。無ければ None、壊れていれば `ManagedBlockError`。

    begin / end がちょうど 1 つずつ、この順で、begin が正規の 1 行（改行は LF か CRLF）であるとき
    だけ受け付ける。
    """
    markers = list(_MARKER_ANY.finditer(text))
    if not markers:
        return None
    kinds = [m.group(1).lower() for m in markers]
    if kinds != ["begin", "end"]:
        raise ManagedBlockError("管理区画の印が重複しているか順序が違う")
    begin = _BEGIN_LINE.match(text, markers[0].start())
    end_at = markers[1].start()
    if begin is None or not text.startswith(MANAGED_END, end_at):
        raise ManagedBlockError("管理区画の印の形が正しくない")
    return ManagedBlock(
        text[: markers[0].start()],
        text[begin.end() : end_at],
        text[end_at + len(MANAGED_END) :],
        int(begin.group(1)),
    )


def merge_managed(existing: str | None, inner: str, generation: int) -> str:
    """existing の管理区画を inner に置き換えた本文。区画が無ければ末尾に足す。"""
    if _MARKER_ANY.search(inner):
        raise ManagedBlockError("区画の中身に管理区画の印がある")
    block = f"{MANAGED_BEGIN} bundle={generation} -->\n{inner}{MANAGED_END}"
    if existing is None or existing == "":
        return block + "\n"
    parts = split_managed(existing)
    if parts is None:
        sep = "\n" if existing.endswith("\n") else "\n\n"
        return existing + sep + block + "\n"
    return parts.before + block + parts.after


def remove_managed(existing: str) -> str:
    """管理区画を取り除いた本文（区画が無ければそのまま）。"""
    parts = split_managed(existing)
    if parts is None:
        return existing
    after = parts.after
    for nl in ("\r\n", "\n"):
        if after.startswith(nl):
            after = after[len(nl) :]
            break
    return parts.before + after


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    exit_code: int
    bundle_generation: int | None = None
    written: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, **asdict(self)}, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 計画の読み取りと事前確認
# ---------------------------------------------------------------------------


@dataclass
class _Change:
    """1 つのパスの置き換え。`text` が None ならファイルを消す。`removes` は削除・区画の除去。"""

    path: str
    target: Path
    text: str | None
    removes: bool = False
    staged: Path | None = None
    backup: Path | None = None


def _require(value: Any, kind: type | tuple[type, ...], what: str) -> Any:
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise PreconditionFailed(f"計画の形が正しくない: {what}")
    return value


def _hash_arg(op: Mapping[str, Any], key: str, *, required: bool) -> str | None:
    value = op.get(key)
    if value is None and not required:
        return None
    return str(_require(value, str, f"{op.get('path')} の {key}"))


def _read_text(target: Path) -> str | None:
    if not target.exists():
        return None
    try:
        return target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise PreconditionFailed(f"{target.name} を UTF-8 として読めない") from e


def _git_config(root: Path) -> Path | None:
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git / "config"
    if not dot_git.is_file():
        return None
    first = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    if not first.startswith("gitdir:"):
        return None
    gitdir = Path(first[len("gitdir:") :].strip())
    if not gitdir.is_absolute():
        gitdir = root / gitdir
    common = gitdir / "commondir"
    if common.is_file():
        rel = common.read_text(encoding="utf-8", errors="replace").strip()
        gitdir = gitdir / rel if not Path(rel).is_absolute() else Path(rel)
    return gitdir / "config"


def origin_full_name(root: Path) -> str | None:
    """`.git` の remote "origin" が GitHub の URL なら `owner/name`。推定できなければ None。"""
    try:
        config = _git_config(root)
        if config is None or not config.is_file():
            return None
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_origin = re.fullmatch(r'\[remote\s+"origin"\]', line) is not None
        elif in_origin and re.match(r"url\s*=", line):
            m = _GITHUB_REMOTE.match(line.split("=", 1)[1].strip())
            return f"{m.group(1)}/{m.group(2)}" if m else None
    return None


def _check_repo(root: Path, plan: Mapping[str, Any], lock: Mapping[str, Any]) -> int:
    generation = _require(plan.get("bundle_generation"), int, "bundle_generation")
    repo_id = _require(plan.get("github_repo_id"), int, "github_repo_id")
    if lock.get("github_repo_id") != repo_id or lock.get("bundle_generation") != generation:
        raise PreconditionFailed("計画と lock のリポジトリ ID か世代が一致しない")
    expected = plan.get("repo_full_name")
    actual = origin_full_name(root)
    if isinstance(expected, str) and actual is not None and actual.lower() != expected.lower():
        raise PreconditionFailed(
            f"この checkout の remote（{actual}）は計画のリポジトリ（{expected}）ではない"
        )
    return int(generation)


def _check_shapes(ops: Sequence[Mapping[str, Any]]) -> None:
    if len(ops) > MAX_FILES:
        raise PreconditionFailed(f"ファイルが多すぎる（{len(ops)} > {MAX_FILES}）")
    seen: dict[str, str] = {}
    total = 0
    for op in ops:
        kind = op.get("op")
        if kind not in _OPS:
            raise PreconditionFailed(f"計画の op が不明: {kind!r}")
        path: str = _require(op.get("path"), str, "path")
        validate_rel_path(path)
        managed = path in MANAGED_FILES
        if managed != (kind in ("set_managed_block", "remove_managed_block")):
            raise PreconditionFailed(f"{path} に {kind} は使えない")
        folded = path.casefold()
        if folded in seen:
            raise PreconditionFailed(f"パスが重複している: {seen[folded]} と {path}")
        seen[folded] = path
        if kind in ("write", "set_managed_block"):
            body: str = _require(op.get("content" if kind == "write" else "inner"), str, "本文")
            size = len(body.encode("utf-8"))
            limit = MAX_MANAGED_BYTES if managed else MAX_FILE_BYTES
            if size > limit:
                raise PreconditionFailed(f"{path} が大きすぎる（{size} バイト）")
            total += size
    if total > MAX_TOTAL_BYTES:
        raise PreconditionFailed(f"合計が大きすぎる（{total} バイト）")
    # 1 つの op のパスが別の op のパスの途中（ディレクトリ）になっていないか
    for path in seen.values():
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i]).casefold()
            if parent in seen:
                raise PreconditionFailed(f"{seen[parent]} がファイルとディレクトリの両方になる")


def _check_lock(ops: Sequence[Mapping[str, Any]], lock: Mapping[str, Any]) -> None:
    files: dict[str, Any] = {}
    absent: list[str] = []
    for op in ops:
        if op["op"] == "write":
            files[op["path"]] = op.get("sha256")
        elif op["op"] == "set_managed_block":
            files[op["path"]] = file_hash(op["inner"])
        else:
            absent.append(op["path"])
    if lock.get("files") != files:
        raise PreconditionFailed("lock の files が計画の書き出しと一致しない")
    if sorted(lock.get("absent") or []) != sorted(absent):
        raise PreconditionFailed("lock の absent が計画の削除と一致しない")


def _decide(root: Path, op: Mapping[str, Any]) -> _Change | None:
    """1 つの op の結果（新しい本文か削除）。何も変えないなら None。前提が崩れれば例外。"""
    kind, path = op["op"], op["path"]
    target = check_target(root, path)
    existing = _read_text(target)
    if kind == "write":
        content, digest = op["content"], _hash_arg(op, "sha256", required=True)
        if file_hash(content) != digest:
            raise PreconditionFailed(f"{path} の中身が計画のハッシュと一致しない")
        if existing is None:
            return _Change(path, target, content)
        current = file_hash(existing)
        if current == digest:
            return None
        if current != _hash_arg(op, "expected_sha256", required=False):
            raise PreconditionFailed(
                f"{path} は手元で変更されているか SpecLino の書き出しではないため上書きしない"
            )
        return _Change(path, target, content)
    if kind == "delete":
        expected = _hash_arg(op, "expected_sha256", required=True)
        if existing is None:
            return None
        if file_hash(existing) != expected:
            raise PreconditionFailed(f"{path} は手元で変更されているため消さない")
        return _Change(path, target, None, removes=True)
    try:
        block = split_managed(existing) if existing is not None else None
        if kind == "remove_managed_block":
            expected = _hash_arg(op, "expected_inner_sha256", required=True)
            if existing is None or block is None:
                return None
            if file_hash(block.inner) != expected:
                raise PreconditionFailed(f"{path} の管理区画が手元で変更されているため取り除かない")
            return _Change(path, target, remove_managed(existing), removes=True)
        inner = op["inner"]
        generation = _require(op.get("generation"), int, f"{path} の generation")
        if block is not None:
            current = file_hash(block.inner)
            if current == file_hash(inner) and block.generation == generation:
                return None
            if current != file_hash(inner) and current != _hash_arg(
                op, "expected_inner_sha256", required=False
            ):
                raise PreconditionFailed(f"{path} の管理区画が手元で変更されているため置き換えない")
        return _Change(path, target, merge_managed(existing, inner, generation))
    except ManagedBlockError as e:
        raise PreconditionFailed(f"{path} の管理区画が壊れているため書き出さない: {e}") from e


# ---------------------------------------------------------------------------
# 段階書き・置き換え・巻き戻し
# ---------------------------------------------------------------------------


def _stage(tmp: Path, changes: list[_Change]) -> list[_Change]:
    for i, change in enumerate(changes):
        if change.text is not None:
            change.staged = tmp / f"f{i}"
            change.staged.write_bytes(change.text.encode("utf-8"))
    return changes


def _make_parents(root: Path, directory: Path) -> list[Path]:
    created = []
    for d in reversed([directory, *directory.parents]):
        if d == root or root not in d.parents or d.exists():
            continue
        d.mkdir()
        created.append(d)
    return created


def _commit(root: Path, tmp: Path, changes: list[_Change]) -> None:
    """changes を順に適用する。途中で失敗したら、適用済みを退避から戻して例外を投げ直す。"""
    done: list[_Change] = []
    created_dirs: list[Path] = []
    try:
        for i, change in enumerate(changes):
            if change.target.exists():
                change.backup = tmp / f"b{i}"
                os.replace(change.target, change.backup)
            done.append(change)
            if change.staged is not None:
                created_dirs += _make_parents(root, change.target.parent)
                os.replace(change.staged, change.target)
    except BaseException as e:
        failed = _rollback(done, created_dirs)
        if failed:
            raise OSError(f"{e}（元に戻せなかったファイル: {', '.join(failed)}）") from e
        raise
    for change in done:
        if change.text is None:
            _prune_empty_parents(root, change.target.parent)


def _rollback(done: list[_Change], created_dirs: list[Path]) -> list[str]:
    """適用済みを逆順に戻す。1 つ戻せなくても残りを続け、戻せなかったパスを返す。"""
    failed: list[str] = []
    for change in reversed(done):
        try:
            if change.backup is not None:
                os.replace(change.backup, change.target)
            elif change.staged is not None and change.target.exists():
                change.target.unlink()
        except OSError as err:
            failed.append(f"{change.path}: {type(err).__name__}")
    for d in reversed(created_dirs):
        with contextlib.suppress(OSError):
            d.rmdir()
    return failed


def _prune_empty_parents(root: Path, directory: Path) -> None:
    """消したファイルの親で、空になったスキル・仕様のディレクトリを消す（置き場の根は残す）。"""
    keep = {root / r for r in SKILL_ROOTS} | {root / SPEC_ROOT}
    d = directory
    while d != root and d not in keep and root in d.parents:
        if d.is_dir():
            if any(d.iterdir()):
                return
            d.rmdir()
        d = d.parent


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _apply(repo_root: Path, plan: Mapping[str, Any], result: ApplyResult) -> None:
    _require(plan, Mapping, "計画が JSON のオブジェクトではない")
    ops = _require(plan.get("ops"), list, "ops")
    for op in ops:
        _require(op, Mapping, "op")
    lock = _require(plan.get("lock"), Mapping, "lock")
    root = repo_root.resolve(strict=True)
    result.bundle_generation = _check_repo(root, plan, lock)
    _check_shapes(ops)
    _check_lock(ops, lock)
    lock_target = check_target(root, LOCK_PATH)

    changes: list[_Change] = []
    for op in ops:
        change = _decide(root, op)
        if change is None:
            result.unchanged.append(op["path"])
        else:
            changes.append(change)
    lock_text = json.dumps(lock, ensure_ascii=False, indent=2) + "\n"
    if _read_text(lock_target) == lock_text:
        result.unchanged.append(LOCK_PATH)
    else:
        changes.append(_Change(LOCK_PATH, lock_target, lock_text))
    if not changes:
        return

    (root / ".speclino").mkdir(exist_ok=True)
    tmp = root / ".speclino" / f"{_TMP_PREFIX}{secrets.token_hex(8)}"
    tmp.mkdir()
    try:
        _stage(tmp, changes)
        # 段階書きの間に置き換えられていないか、置き換えの直前に確かめ直す
        for change in changes:
            if check_target(root, change.path) != change.target:
                raise PreconditionFailed(f"書き出し先が変わった: {change.path}")
        _commit(root, tmp, changes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for change in changes:
        (result.deleted if change.removes else result.written).append(change.path)


def apply_plan(repo_root: Path, plan: Mapping[str, Any]) -> ApplyResult:
    """計画を repo_root に当てる。終了コード 0 / 2（前提が崩れ何もしていない）/ 1（元に戻した）。"""
    result = ApplyResult(EXIT_OK)
    try:
        _apply(repo_root, plan, result)
    except PreconditionFailed as e:
        return ApplyResult(EXIT_PRECONDITION, result.bundle_generation, reason=str(e))
    except Exception as e:  # noqa: BLE001 — 予期しない失敗も結果の JSON で返す
        return ApplyResult(EXIT_FAILED, result.bundle_generation, reason=f"{type(e).__name__}: {e}")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="speclino_apply.py", description="SpecLino の書き出し計画を checkout に当てる"
    )
    parser.add_argument("plan", help="計画の JSON ファイル（- なら標準入力）")
    parser.add_argument("--repo", default=".", help="checkout のルート（既定は現在のディレクトリ）")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.plan == "-" else Path(args.plan).read_text(encoding="utf-8")
        plan = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        result = ApplyResult(EXIT_PRECONDITION, reason=f"計画の JSON を読めない: {e}")
    else:
        result = apply_plan(Path(args.repo), plan)
    print(result.to_json())
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
