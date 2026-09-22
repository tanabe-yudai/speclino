"""CloudTasksQueue の単体試験（S26）。実際の GCP は呼ばず、フェイクの非同期クライアントで検証する。

タスク名の決定性・body/OIDC/deadline の組み立て・AlreadyExists の吸収を確認する。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import pytest
from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2

from speclino.adapters.queue.cloud_tasks import CloudTasksQueue

PROJECT = "proj"
LOCATION = "asia-northeast1"
QUEUE = "speclino-runs"
WORKER_URL = "https://speclino-worker-abc.example.run.app"
OIDC_EMAIL = "speclino-tasks@proj.iam.gserviceaccount.com"


@dataclass
class _FakeTasksClient:
    """CloudTasksAsyncClient の代わりに使う。create_task の呼出しを記録する。"""

    raise_already_exists: bool = False
    created: list[tasks_v2.Task] = field(default_factory=list)

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
        if self.raise_already_exists:
            raise AlreadyExists("task already exists")
        self.created.append(task)
        return task


def _expected_task_name(run_id: uuid.UUID) -> str:
    return f"projects/{PROJECT}/locations/{LOCATION}/queues/{QUEUE}/tasks/run-{run_id}"


def _queue(client: _FakeTasksClient, **overrides: object) -> CloudTasksQueue:
    kwargs: dict[str, object] = {
        "project": PROJECT,
        "location": LOCATION,
        "queue": QUEUE,
        "worker_url": WORKER_URL,
        "oidc_service_account_email": OIDC_EMAIL,
        "client": client,
    }
    kwargs.update(overrides)
    return CloudTasksQueue(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_name_is_deterministic_from_run_id() -> None:
    run_id = uuid.uuid4()
    client = _FakeTasksClient()

    name = await _queue(client).enqueue(run_id)

    assert name == _expected_task_name(run_id)
    assert client.created[0].name == _expected_task_name(run_id)


@pytest.mark.asyncio
async def test_same_run_id_enqueued_twice_yields_same_task_name() -> None:
    run_id = uuid.uuid4()
    client = _FakeTasksClient()

    first = await _queue(client).enqueue(run_id)
    second = await _queue(client).enqueue(run_id)

    assert first == second == _expected_task_name(run_id)


@pytest.mark.asyncio
async def test_task_body_oidc_and_deadline_are_assembled_as_expected() -> None:
    run_id = uuid.uuid4()
    client = _FakeTasksClient()

    await _queue(client).enqueue(run_id)

    task = client.created[0]
    req = task.http_request
    assert req.url == f"{WORKER_URL}/internal/tasks/run"
    assert req.http_method == tasks_v2.HttpMethod.POST
    assert dict(req.headers) == {"Content-Type": "application/json"}
    assert json.loads(req.body) == {"run_id": str(run_id)}
    assert req.oidc_token.service_account_email == OIDC_EMAIL
    assert req.oidc_token.audience == f"{WORKER_URL}/internal/tasks/run"
    assert task.dispatch_deadline.seconds == 1500


@pytest.mark.asyncio
async def test_dispatch_deadline_is_configurable() -> None:
    run_id = uuid.uuid4()
    client = _FakeTasksClient()

    await _queue(client, dispatch_deadline_s=900).enqueue(run_id)

    assert client.created[0].dispatch_deadline.seconds == 900


@pytest.mark.asyncio
async def test_worker_url_trailing_slash_does_not_duplicate_path() -> None:
    run_id = uuid.uuid4()
    client = _FakeTasksClient()

    await _queue(client, worker_url=WORKER_URL + "/").enqueue(run_id)

    assert client.created[0].http_request.url == f"{WORKER_URL}/internal/tasks/run"


@pytest.mark.asyncio
async def test_duplicate_enqueue_returns_existing_task_name_on_already_exists() -> None:
    """webhook の再配信で同じ run_id が二重投入されても Cloud Tasks の AlreadyExists を
    エラーにせず、既存タスク名をそのまま返す（design の「時間と冪等性」）。"""
    run_id = uuid.uuid4()
    client = _FakeTasksClient(raise_already_exists=True)

    name = await _queue(client).enqueue(run_id)

    assert name == _expected_task_name(run_id)
    assert client.created == []  # 実際には作られていない（既存を再利用しただけ）


def test_client_is_created_lazily() -> None:
    """コンストラクタでは CloudTasksAsyncClient を作らない（ADC の無い環境でも構築できる）。"""
    queue = CloudTasksQueue(
        project=PROJECT,
        location=LOCATION,
        queue=QUEUE,
        worker_url=WORKER_URL,
        oidc_service_account_email=OIDC_EMAIL,
    )

    assert queue._client is None
