"""GitHub webhook の署名検証とイベント抽出（S19）。

署名: `X-Hub-Signature-256` = `"sha256=" + HMAC_SHA256(secret, raw_body).hexdigest()`。
`hmac.compare_digest` でタイミング攻撃を避ける。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from speclino.adapters.github.types import IssueCommentRef, PullRequestRef, RerunRef, WebhookEvent

_PR_ACTIONS = frozenset({"opened", "synchronize", "reopened"})
_CHECK_RUN_RERUN_ACTIONS = frozenset({"rerequested", "requested_action"})


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """`signature_header` が `body` を `secret` で HMAC-SHA256 署名したものと一致するか。"""
    if signature_header is None or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, signature_header)


def parse_event(headers: Mapping[str, str], body: bytes) -> WebhookEvent:
    """ヘッダ（大文字小文字を無視）と生 body から WebhookEvent を組み立てる。署名検証は別関数。"""
    lower_headers = {k.lower(): v for k, v in headers.items()}
    payload = json.loads(body)
    payload = payload if isinstance(payload, dict) else {}
    action = payload.get("action")
    return WebhookEvent(
        delivery_id=lower_headers.get("x-github-delivery", ""),
        event=lower_headers.get("x-github-event", ""),
        action=action if isinstance(action, str) else None,
        payload=payload,
    )


def extract_pr(payload: dict[str, object]) -> PullRequestRef | None:
    """pull_request イベントの opened / synchronize / reopened だけを抽出する。"""
    if payload.get("action") not in _PR_ACTIONS:
        return None
    pr = _as_dict(payload.get("pull_request"))
    repo = _as_dict(payload.get("repository"))
    installation = _as_dict(payload.get("installation"))
    if not pr or not repo or not installation:
        return None
    installation_id = _as_int(installation.get("id"))
    pr_number = _as_int(pr.get("number"))
    if installation_id is None or pr_number is None:
        return None

    head = _as_dict(pr.get("head"))
    base = _as_dict(pr.get("base"))
    head_repo = _as_dict(head.get("repo"))
    user = _as_dict(pr.get("user"))
    repo_full_name = _as_str(repo.get("full_name"))
    head_repo_full_name = _as_str(head_repo.get("full_name"))
    return PullRequestRef(
        repo_full_name=repo_full_name,
        installation_id=installation_id,
        pr_number=pr_number,
        head_sha=_as_str(head.get("sha")),
        base_sha=_as_str(base.get("sha")),
        head_repo_full_name=head_repo_full_name,
        is_fork=head_repo_full_name != repo_full_name,
        author_login=_as_str(user.get("login")),
        html_url=_as_str(pr.get("html_url")),
    )


def extract_comment(payload: dict[str, object]) -> IssueCommentRef | None:
    """issue_comment イベントの created だけを抽出する。"""
    if payload.get("action") != "created":
        return None
    comment = _as_dict(payload.get("comment"))
    issue = _as_dict(payload.get("issue"))
    repo = _as_dict(payload.get("repository"))
    installation = _as_dict(payload.get("installation"))
    if not comment or not issue or not repo or not installation:
        return None
    installation_id = _as_int(installation.get("id"))
    issue_number = _as_int(issue.get("number"))
    if installation_id is None or issue_number is None:
        return None

    user = _as_dict(comment.get("user"))
    return IssueCommentRef(
        repo_full_name=_as_str(repo.get("full_name")),
        installation_id=installation_id,
        issue_number=issue_number,
        body=_as_str(comment.get("body")),
        author_login=_as_str(user.get("login")),
        comment_url=_as_str(comment.get("html_url")),
        is_pull_request="pull_request" in issue,
    )


def _pr_numbers(check_suite: dict[str, object]) -> list[int]:
    prs = check_suite.get("pull_requests")
    if not isinstance(prs, list):
        return []
    numbers: list[int] = []
    for item in prs:
        n = _as_int(_as_dict(item).get("number"))
        if n is not None:
            numbers.append(n)
    return numbers


def extract_rerun(payload: dict[str, object]) -> RerunRef | None:
    """check_run(rerequested/requested_action) または check_suite(rerequested) を抽出する。"""
    action = payload.get("action")
    repo = _as_dict(payload.get("repository"))
    installation = _as_dict(payload.get("installation"))
    if not repo or not installation:
        return None
    installation_id = _as_int(installation.get("id"))
    if installation_id is None:
        return None
    repo_full_name = _as_str(repo.get("full_name"))

    check_run = _as_dict(payload.get("check_run"))
    if check_run and action in _CHECK_RUN_RERUN_ACTIONS:
        check_suite = _as_dict(check_run.get("check_suite"))
        return RerunRef(
            repo_full_name=repo_full_name,
            installation_id=installation_id,
            head_sha=_as_str(check_suite.get("head_sha")),
            pr_numbers=_pr_numbers(check_suite),
            check_run_id=_as_int(check_run.get("id")),
        )

    check_suite = _as_dict(payload.get("check_suite"))
    if check_suite and action == "rerequested":
        return RerunRef(
            repo_full_name=repo_full_name,
            installation_id=installation_id,
            head_sha=_as_str(check_suite.get("head_sha")),
            pr_numbers=_pr_numbers(check_suite),
            check_run_id=None,
        )
    return None


__all__ = [
    "extract_comment",
    "extract_pr",
    "extract_rerun",
    "parse_event",
    "verify_signature",
]
