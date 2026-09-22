"""テスト・CLI 向けの最小 Snapshot 実装。対象ディレクトリの読取りだけを行う。

Makefile を含め、対象リポジトリの何かを実行することはない（構文的な読み取りのみ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DirectorySnapshot:
    """root 配下をファイルシステムから直接読む Snapshot。"""

    root: Path
    snapshot_id: str = "directory"

    def read_text(self, path: str) -> str | None:
        target = self.root / path
        if not target.is_file():
            return None
        return target.read_text(encoding="utf-8")

    def glob(self, pattern: str) -> list[str]:
        return sorted(
            p.relative_to(self.root).as_posix() for p in self.root.glob(pattern) if p.is_file()
        )
