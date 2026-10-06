"""CodeHost ポート（S19）。エージェント/サービス層が使う、コードホスト（GitHub 等）への抽象。

GitHub 固有の実装は `speclino.adapters.github` に置く。この層は Protocol のみで、
HTTP・認証・リトライの詳細を知らない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class CodeHost(Protocol):
    """PR への Check runs 作成・更新、コメント投稿、スナップショット取得を抽象化する。"""

    async def create_check(
        self,
        repo: str,
        head_sha: str,
        name: str,
        details_url: str | None,
        external_id: str | None,
    ) -> int:
        """Check run を作成し、check_run_id を返す。"""
        ...

    async def update_check(
        self,
        repo: str,
        check_run_id: int,
        status: str,
        conclusion: str | None,
        title: str,
        summary: str,
        text: str,
        annotations: list[dict[str, object]],
    ) -> None:
        """Check run を更新する。annotations は 50 件ずつに分割して複数回 PATCH する。"""
        ...

    async def post_comment(self, repo: str, issue_number: int, body: str) -> int:
        """Issue/PR にコメントを投稿し、コメント ID を返す。"""
        ...

    async def upsert_comment(self, repo: str, issue_number: int, marker: str, body: str) -> int:
        """`marker` を含む既存コメントがあれば更新し、無ければ投稿する。

        push のたびにコメントが増えないようにするため、判定の要約はこれで出す。
        """
        ...

    async def fetch_snapshot(self, repo: str, sha: str, dest_dir: Path) -> Path:
        """repo の sha 時点のスナップショットを dest_dir に展開し、最上位ディレクトリを返す。"""
        ...

    async def repository_id(self, repo: str) -> int | None:
        """repo（`owner/name`）の今のリポジトリ ID。存在しない・読めないなら None。

        名前は改名後に別リポジトリへ再利用されうるので、名前で取得する前に、保存してある ID と
        一致するかをこれで確かめる（S53）。
        """
        ...

    async def branch_head(self, repo: str, branch: str) -> str | None:
        """repo の branch の今の先端のコミット SHA。無い・読めないなら None。

        bundle の run（S65）が信頼側（デフォルトブランチ）を、リクエストの値に頼らず自分で決める。
        """
        ...


__all__ = ["CodeHost"]
