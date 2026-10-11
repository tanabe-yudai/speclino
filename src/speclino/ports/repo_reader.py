"""RepoReader ポート: 登録済みのリポジトリのファイルを読むだけ（知識の会話の調査）。

GitHub の実装は adapters/github/client.py（App の認証を持つ worker）と
adapters/worker/repo_reader_client.py（web から worker に任せる）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepoTreeEntry:
    """git tree の blob 1 件。`mode` は git のファイルモード（100644 / 100755 / 120000 等）。"""

    path: str
    mode: str
    sha: str
    size: int | None = None


@dataclass(frozen=True)
class RepoTree:
    entries: list[RepoTreeEntry]
    # 事業者の上限で一覧が途中までしか無い
    truncated: bool = False


class RepoReader(Protocol):
    async def repository_id(self, repo: str) -> int | None:
        """repo（`owner/name`）の今のリポジトリ ID。読めないなら None。"""
        ...

    async def branch_head(self, repo: str, branch: str) -> str | None:
        """branch の先端のコミット SHA（40 桁）。無い・読めないなら None。"""
        ...

    async def tree(self, repo: str, commit_sha: str) -> RepoTree:
        """commit の木の blob の一覧（再帰）。"""
        ...

    async def blob(self, repo: str, blob_sha: str) -> bytes:
        """blob の中身。`blob_sha` は木から引いた 40 桁の SHA だけを渡す。"""
        ...


__all__ = ["RepoReader", "RepoTree", "RepoTreeEntry"]
