"""localgit: ローカル git リポジトリを読むアダプター。"""

from speclino.adapters.localgit.code_host import WORKTREE, GitError, export_snapshot, resolve_sha

__all__ = ["WORKTREE", "GitError", "export_snapshot", "resolve_sha"]
