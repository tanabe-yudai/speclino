"""localgit: ローカル git リポジトリからソーススナップショットを解決・展開する。

`speclino check` の CLI 層が使う。呼び出せる外部プロセスは `git` だけに限定する
（アーカイブの展開は `tarfile`（標準ライブラリ）で行い、`tar` コマンドは呼ばない）。

`WORKTREE` は「作業ツリーそのものを使う」ことを表す特別なリファレンス値。コミットではないので
`resolve_sha` はそのまま返し、`export_snapshot` は何も展開せず `repo_root` を返す。それ以外は
`git archive <sha>` が返すコミットの内容だけを使い、作業ツリーの未コミット変更は使わない。
"""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

WORKTREE = "WORKTREE"


class GitError(RuntimeError):
    """git の実行・解決に失敗した（CLI では終了コード2）。"""


def resolve_sha(repo_root: Path, ref: str) -> str:
    """`ref`（ブランチ／タグ／コミット／`HEAD` 等）を解決済みコミット SHA にする。

    `WORKTREE` は作業ツリーを指す特別値としてそのまま返す（解決しない）。
    """
    if ref == WORKTREE:
        return ref
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        raise GitError(f"git を実行できない: {e}") from e
    if proc.returncode != 0:
        raise GitError(f"git rev-parse {ref!r} が失敗: {proc.stderr.strip()}")
    sha = proc.stdout.strip()
    if not sha:
        raise GitError(f"git rev-parse {ref!r} が SHA を返さなかった")
    return sha


def export_snapshot(repo_root: Path, sha: str, dest: Path) -> Path:
    """`sha` 時点のコミットの内容を `dest` へ展開する。

    作業ツリーのファイルではなく `git archive` が返すコミットの内容を使う。
    `sha` が `WORKTREE` のときだけ何も展開せず `repo_root` をそのまま返す。
    """
    if sha == WORKTREE:
        return repo_root
    dest.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(repo_root), "archive", sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        raise GitError(f"git を実行できない: {e}") from e
    assert proc.stdout is not None
    extract_error: str | None = None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(dest, filter="data")
    except tarfile.TarError as e:
        extract_error = str(e)
    finally:
        _, stderr = proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or extract_error or "unknown error"
        raise GitError(f"git archive {sha!r} が失敗: {detail}")
    if extract_error is not None:
        raise GitError(f"アーカイブの展開に失敗: {extract_error}")
    return dest


__all__ = ["WORKTREE", "GitError", "export_snapshot", "resolve_sha"]
