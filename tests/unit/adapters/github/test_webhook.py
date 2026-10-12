"""webhook の単体試験（S19）。署名検証とイベント抽出を実データに近いペイロードで確認する。"""

from __future__ import annotations

import hashlib
import hmac
import json

from speclino.adapters.github.webhook import (
    extract_comment,
    extract_pr,
    extract_rerun,
    extract_review_approval,
    parse_event,
    verify_signature,
)

SECRET = "test-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------- verify_signature


def test_verify_signature_accepts_correct_signature() -> None:
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, _sign(body)) is True


def test_verify_signature_rejects_wrong_signature() -> None:
    body = b'{"a": 1}'
    wrong = _sign(body, secret="other-secret")
    assert verify_signature(SECRET, body, wrong) is False


def test_verify_signature_rejects_none_header() -> None:
    assert verify_signature(SECRET, b"{}", None) is False


def test_verify_signature_rejects_missing_prefix() -> None:
    body = b'{"a": 1}'
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_signature(SECRET, body, digest) is False


# ---------------------------------------------------------------- parse_event


def test_parse_event_reads_headers_case_insensitively() -> None:
    body = json.dumps({"action": "opened"}).encode("utf-8")
    headers = {
        "X-GitHub-Delivery": "abc-123",
        "x-github-event": "pull_request",
        "Content-Type": "application/json",
    }
    event = parse_event(headers, body)
    assert event.delivery_id == "abc-123"
    assert event.event == "pull_request"
    assert event.action == "opened"
    assert event.payload == {"action": "opened"}


def test_parse_event_lowercase_headers_also_work() -> None:
    body = json.dumps({"action": "created"}).encode("utf-8")
    headers = {"x-github-delivery": "d-1", "x-github-event": "issue_comment"}
    event = parse_event(headers, body)
    assert event.delivery_id == "d-1"
    assert event.event == "issue_comment"


def test_parse_event_missing_action_is_none() -> None:
    body = json.dumps({"foo": "bar"}).encode("utf-8")
    event = parse_event({}, body)
    assert event.action is None
    assert event.delivery_id == ""
    assert event.event == ""


# ---------------------------------------------------------------- extract_pr


def _pr_payload(
    action: str = "opened",
    repo_full_name: str = "acme/widgets",
    head_repo_full_name: str | None = "acme/widgets",
) -> dict[str, object]:
    head_repo: dict[str, object] | None
    head_repo = {"full_name": head_repo_full_name} if head_repo_full_name is not None else None
    return {
        "action": action,
        "installation": {"id": 42},
        "repository": {"full_name": repo_full_name, "id": 4242},
        "pull_request": {
            "number": 7,
            "html_url": "https://github.com/acme/widgets/pull/7",
            "user": {"login": "octocat"},
            "head": {"sha": "head-sha-1", "repo": head_repo},
            "base": {"sha": "base-sha-1"},
        },
    }


def test_extract_pr_opened_same_repo_is_not_fork() -> None:
    ref = extract_pr(_pr_payload(action="opened"))
    assert ref is not None
    assert ref.repo_full_name == "acme/widgets"
    assert ref.repo_id == 4242
    assert ref.installation_id == 42
    assert ref.pr_number == 7
    assert ref.head_sha == "head-sha-1"
    assert ref.base_sha == "base-sha-1"
    assert ref.head_repo_full_name == "acme/widgets"
    assert ref.is_fork is False
    assert ref.author_login == "octocat"
    assert ref.html_url == "https://github.com/acme/widgets/pull/7"


def test_extract_pr_synchronize_and_reopened_are_accepted() -> None:
    assert extract_pr(_pr_payload(action="synchronize")) is not None
    assert extract_pr(_pr_payload(action="reopened")) is not None


def test_extract_pr_detects_fork() -> None:
    ref = extract_pr(_pr_payload(head_repo_full_name="someone-else/widgets"))
    assert ref is not None
    assert ref.is_fork is True
    assert ref.head_repo_full_name == "someone-else/widgets"


def test_events_without_a_repository_id_are_not_extracted() -> None:
    """S53: project の同一性はリポジトリ ID で決めるので、ID の無いイベントは処理しない。"""
    payload = _pr_payload()
    payload["repository"] = {"full_name": "acme/widgets"}
    assert extract_pr(payload) is None


def test_extract_pr_ignores_unrelated_action() -> None:
    assert extract_pr(_pr_payload(action="closed")) is None


def test_extract_pr_missing_required_fields_returns_none() -> None:
    assert extract_pr({"action": "opened"}) is None


# ---------------------------------------------------------------- extract_comment


def _comment_payload(
    action: str = "created", issue_extra: dict[str, object] | None = None
) -> dict[str, object]:
    issue: dict[str, object] = {"number": 9}
    if issue_extra:
        issue.update(issue_extra)
    return {
        "action": action,
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "issue": issue,
        "comment": {
            "body": "/speclino approve-spec sha256:abc",
            "html_url": "https://github.com/acme/widgets/issues/9#issuecomment-1",
            "user": {"login": "reviewer", "id": 5150},
        },
    }


def test_extract_comment_on_pull_request() -> None:
    payload = _comment_payload(issue_extra={"pull_request": {"url": "..."}})
    ref = extract_comment(payload)
    assert ref is not None
    assert ref.repo_full_name == "acme/widgets"
    assert ref.installation_id == 42
    assert ref.issue_number == 9
    assert ref.body == "/speclino approve-spec sha256:abc"
    assert ref.author_login == "reviewer"
    assert ref.author_id == 5150
    assert ref.comment_url == "https://github.com/acme/widgets/issues/9#issuecomment-1"
    assert ref.is_pull_request is True


def test_extract_comment_on_plain_issue() -> None:
    ref = extract_comment(_comment_payload())
    assert ref is not None
    assert ref.is_pull_request is False


def test_extract_comment_without_user_id_has_no_author_id() -> None:
    """ID の無いペイロードは author_id=None（承認しない側に倒すのは照合側）。"""
    payload = _comment_payload()
    comment = payload["comment"]
    assert isinstance(comment, dict)
    comment["user"] = {"login": "reviewer"}
    ref = extract_comment(payload)
    assert ref is not None and ref.author_id is None


def test_extract_comment_ignores_non_created_action() -> None:
    assert extract_comment(_comment_payload(action="edited")) is None


def test_extract_comment_missing_required_fields_returns_none() -> None:
    assert extract_comment({"action": "created"}) is None


# ---------------------------------------------------------------- extract_rerun


def test_extract_rerun_from_check_run_rerequested() -> None:
    payload: dict[str, object] = {
        "action": "rerequested",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "check_run": {
            "id": 555,
            "check_suite": {
                "head_sha": "sha-rerun",
                "pull_requests": [{"number": 7}, {"number": 8}],
            },
        },
    }
    ref = extract_rerun(payload)
    assert ref is not None
    assert ref.repo_full_name == "acme/widgets"
    assert ref.installation_id == 42
    assert ref.head_sha == "sha-rerun"
    assert ref.pr_numbers == [7, 8]
    assert ref.check_run_id == 555


def test_extract_rerun_from_check_run_requested_action() -> None:
    payload: dict[str, object] = {
        "action": "requested_action",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "check_run": {
            "id": 556,
            "check_suite": {"head_sha": "sha-2", "pull_requests": []},
        },
        "requested_action": {"identifier": "approve"},
    }
    ref = extract_rerun(payload)
    assert ref is not None
    assert ref.check_run_id == 556
    assert ref.pr_numbers == []


def test_extract_rerun_from_check_suite_rerequested() -> None:
    payload: dict[str, object] = {
        "action": "rerequested",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "check_suite": {
            "head_sha": "sha-suite",
            "pull_requests": [{"number": 3}],
        },
    }
    ref = extract_rerun(payload)
    assert ref is not None
    assert ref.head_sha == "sha-suite"
    assert ref.pr_numbers == [3]
    assert ref.check_run_id is None


def test_extract_rerun_ignores_unrelated_event() -> None:
    payload: dict[str, object] = {
        "action": "opened",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "pull_request": {"number": 1},
    }
    assert extract_rerun(payload) is None


# ---------------------------------------------------------------- extract_review_approval


def _review_payload(action: str = "submitted", state: str = "approved") -> dict[str, object]:
    return {
        "action": action,
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "id": 4242},
        "review": {
            "state": state,
            "commit_id": "sha-head",
            "html_url": "https://github.com/acme/widgets/pull/7#pullrequestreview-1",
            "user": {"login": "lead", "id": 900},
        },
        "pull_request": {"number": 7, "user": {"login": "dev", "id": 100}},
    }


def test_extract_review_approval_reads_approved_submission() -> None:
    ref = extract_review_approval(_review_payload())
    assert ref is not None
    assert (ref.repo_id, ref.pr_number, ref.commit_id) == (4242, 7, "sha-head")
    assert (ref.reviewer_login, ref.reviewer_id, ref.pr_author_id) == ("lead", 900, 100)
    assert ref.review_url.endswith("pullrequestreview-1")


def test_extract_review_approval_ignores_other_states_and_actions() -> None:
    for state in ("commented", "changes_requested", "dismissed"):
        assert extract_review_approval(_review_payload(state=state)) is None
    for action in ("dismissed", "edited"):
        assert extract_review_approval(_review_payload(action=action)) is None


def test_extract_review_approval_requires_commit_and_ids() -> None:
    payload = _review_payload()
    payload["review"] = {**payload["review"], "commit_id": ""}  # type: ignore[dict-item]
    assert extract_review_approval(payload) is None
    payload = _review_payload()
    payload["repository"] = {"full_name": "acme/widgets"}
    assert extract_review_approval(payload) is None
    # レビューした人の ID が無ければ None のまま渡す（照合側で承認しない）
    payload = _review_payload()
    payload["review"] = {**payload["review"], "user": {"login": "lead"}}  # type: ignore[dict-item]
    ref = extract_review_approval(payload)
    assert ref is not None and ref.reviewer_id is None
