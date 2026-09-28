"""GitHub アダプター固有の型（S19）。domain/ の IR とは別系統（GitHub API の素直な写像）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _GitHubModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WebhookEvent(_GitHubModel):
    """検証済みの GitHub webhook 1 件。"""

    delivery_id: str
    event: str
    action: str | None
    payload: dict[str, object]


class PullRequestRef(_GitHubModel):
    """PR イベント（opened / synchronize / reopened）から抽出した参照。"""

    repo_full_name: str
    # GitHub のリポジトリ ID（S53）。名前は改名後に別リポジトリへ再利用されうるので、project の
    # 同一性はこちらで決める。
    repo_id: int
    installation_id: int
    pr_number: int
    head_sha: str
    base_sha: str
    head_repo_full_name: str
    is_fork: bool
    author_login: str
    html_url: str


class IssueCommentRef(_GitHubModel):
    """issue_comment(created) イベントから抽出した参照。"""

    repo_full_name: str
    repo_id: int
    installation_id: int
    issue_number: int
    body: str
    author_login: str
    comment_url: str
    is_pull_request: bool


class RerunRef(_GitHubModel):
    """check_run(rerequested/requested_action) または check_suite(rerequested) からの再実行要求。"""

    repo_full_name: str
    repo_id: int
    installation_id: int
    head_sha: str
    pr_numbers: list[int]
    check_run_id: int | None


__all__ = [
    "IssueCommentRef",
    "PullRequestRef",
    "RerunRef",
    "WebhookEvent",
]
