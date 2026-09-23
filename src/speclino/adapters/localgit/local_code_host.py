"""ローカル git リポジトリを CodeHost として扱う（GitHub なしの E2E 用）。

Check / コメントはメモリに記録するだけ。スナップショットは `git archive` で展開する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from speclino.adapters.localgit.code_host import export_snapshot


@dataclass
class LocalCodeHost:
    repos: dict[str, Path]  # repo_full_name → ローカルの git リポジトリ
    checks: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    _next_id: int = 1

    async def create_check(
        self,
        repo: str,
        head_sha: str,
        name: str,
        details_url: str | None,
        external_id: str | None,
    ) -> int:
        cid = self._next_id
        self._next_id += 1
        self.checks[cid] = {
            "repo": repo,
            "head_sha": head_sha,
            "name": name,
            "details_url": details_url,
            "external_id": external_id,
            "status": "queued",
            "conclusion": None,
            "updates": 0,
        }
        return cid

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
        c = self.checks[check_run_id]
        c.update(
            {
                "status": status,
                "conclusion": conclusion,
                "title": title,
                "summary": summary,
                "text": text,
                "annotations": annotations,
                "updates": c["updates"] + 1,
            }
        )

    async def upsert_comment(self, repo: str, issue_number: int, marker: str, body: str) -> int:
        for existing in self.comments:
            if existing["issue_number"] == issue_number and marker in str(existing["body"]):
                existing["body"] = body
                return int(existing["id"])
        return await self.post_comment(repo, issue_number, body)

    async def post_comment(self, repo: str, issue_number: int, body: str) -> int:
        comment_id = len(self.comments) + 1
        self.comments.append(
            {"id": comment_id, "repo": repo, "issue_number": issue_number, "body": body}
        )
        return comment_id

    async def fetch_snapshot(self, repo: str, sha: str, dest_dir: Path) -> Path:
        root = self.repos[repo]
        return export_snapshot(root, sha, dest_dir / sha[:12])
