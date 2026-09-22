"""localgit: ローカル git リポジトリを読むアダプター（PoC 15章）。"""

from speclino.adapters.localgit.code_host import WORKTREE, GitError, export_snapshot, resolve_sha

__all__ = ["WORKTREE", "GitError", "export_snapshot", "resolve_sha"]
