"""build_state（api/deps.py）の kind 分岐の単体試験（S26）。

Cloud Tasks / GCS への実接続はしない。CloudTasksQueue / GcsBlobStore はどちらもクライアントを
遅延生成するため、build_state 自体は ADC 無しの環境でも呼べる（構築するだけなら RPC しない）。
"""

from __future__ import annotations

import pytest

from speclino.adapters.github.client import GitHubClient
from speclino.adapters.github.user_lookup import PublicGitHubUserLookup
from speclino.adapters.queue.cloud_tasks import CloudTasksQueue
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.adapters.storage.gcs import GcsBlobStore
from speclino.adapters.worker.github_user_client import WorkerGitHubUserLookup
from speclino.api.deps import build_state
from speclino.settings import Settings

# 形だけの PEM（build_state は鍵を読むだけで署名しない）
PRIVATE_KEY_PEM = "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"

CLOUD_TASKS_ENV = dict(
    cloud_tasks_project="proj",
    cloud_tasks_location="asia-northeast1",
    cloud_tasks_queue="speclino-runs",
    cloud_tasks_worker_url="https://speclino-worker-abc.example.run.app",
    cloud_tasks_oidc_service_account_email="speclino-tasks@proj.iam.gserviceaccount.com",
)


def test_default_settings_wire_inprocess_queue_and_fs_blobs() -> None:
    state = build_state(Settings())

    assert isinstance(state.queue, InProcessQueue)
    assert isinstance(state.blobs, FileSystemBlobStore)


def test_cloud_tasks_kind_wires_cloud_tasks_queue_when_fully_configured() -> None:
    state = build_state(Settings(queue_kind="cloud_tasks", **CLOUD_TASKS_ENV))

    assert isinstance(state.queue, CloudTasksQueue)
    assert state.queue.project == "proj"
    assert state.queue.location == "asia-northeast1"
    assert state.queue.queue == "speclino-runs"
    assert state.queue.worker_url == (
        "https://speclino-worker-abc.example.run.app/internal/tasks/run"
    )
    assert state.queue.oidc_service_account_email == ("speclino-tasks@proj.iam.gserviceaccount.com")


@pytest.mark.parametrize("missing", list(CLOUD_TASKS_ENV))
def test_cloud_tasks_kind_raises_when_one_setting_is_missing(missing: str) -> None:
    env = dict(CLOUD_TASKS_ENV)
    env[missing] = None

    with pytest.raises(RuntimeError, match="cloud_tasks"):
        build_state(Settings(queue_kind="cloud_tasks", **env))


def test_gcs_kind_wires_gcs_blob_store_when_bucket_is_set() -> None:
    state = build_state(Settings(blob_kind="gcs", gcs_bucket="speclino-artifacts"))

    assert isinstance(state.blobs, GcsBlobStore)
    assert state.blobs.bucket == "speclino-artifacts"


def test_gcs_kind_raises_when_bucket_is_missing() -> None:
    with pytest.raises(RuntimeError, match="gcs"):
        build_state(Settings(blob_kind="gcs"))


# --- 承認者のユーザー ID の問い合わせ先（S61） ----------------------------------------------


def test_web_with_a_worker_url_delegates_the_user_lookup_to_the_worker() -> None:
    """web は秘密鍵を持たず、認証なしの公開 API は共有 IP でレート制限に当たる。"""
    worker_url = "https://speclino-worker-abc.example.run.app"
    state = build_state(Settings(role="web", cloud_tasks_worker_url=worker_url))

    assert isinstance(state.github_users, WorkerGitHubUserLookup)
    assert state.github_users.worker_url == worker_url


def test_with_the_app_key_the_user_lookup_uses_the_github_app() -> None:
    state = build_state(
        Settings(role="worker", github_app_id=1, github_private_key=PRIVATE_KEY_PEM)
    )

    assert isinstance(state.github_users, GitHubClient)


def test_without_the_app_key_the_user_lookup_falls_back_to_the_public_api() -> None:
    state = build_state(Settings(role="all"))

    assert isinstance(state.github_users, PublicGitHubUserLookup)
