"""Cloud Tasks の TaskQueue（本番用、S26）。

web が Cloud Tasks に HTTP タスクを作り、worker の `POST /internal/tasks/run` を OIDC で呼ぶ
（`infra/envs/prod/tasks.tf` でキューを Terraform 済み。呼出し元の IAM は `run.invoker` を
tasks SA に限定して worker 側で強制する）。非同期クライアント（`CloudTasksAsyncClient`）を使う。
"""

from __future__ import annotations

import json
from uuid import UUID

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from google.protobuf import duration_pb2


class CloudTasksQueue:
    def __init__(
        self,
        project: str,
        location: str,
        queue: str,
        worker_url: str,
        oidc_service_account_email: str,
        dispatch_deadline_s: int = 1500,
        client: tasks_v2.CloudTasksAsyncClient | None = None,
    ) -> None:
        """dispatch_deadline_s の既定 1500 秒（25分）の根拠は infra/envs/prod/tasks.tf 冒頭の
        コメント:
        パイプライン全体 15分 < Cloud Run request timeout 20分 < dispatch deadline 25分。
        """
        self.project = project
        self.location = location
        self.queue = queue
        # worker サービスの URL（Cloud Run の URI そのまま）に内部エンドポイントを付ける。
        self.worker_url = worker_url.rstrip("/") + "/internal/tasks/run"
        self.oidc_service_account_email = oidc_service_account_email
        self.dispatch_deadline_s = dispatch_deadline_s
        self._client = client

    @property
    def client(self) -> tasks_v2.CloudTasksAsyncClient:
        """クライアントは遅延生成する。

        `CloudTasksAsyncClient()` の生成自体が既定の認証情報（ADC）を要求するため、起動時
        （`api/deps.py` の `build_state`）に作ると ADC の無い環境で落ちる。`api/deps.py` の
        `LazyCodeHost` と同じ理由。テストでは `client` を注入して差し替える。
        """
        if self._client is None:
            self._client = tasks_v2.CloudTasksAsyncClient()
        return self._client

    async def enqueue(self, run_id: UUID) -> str:
        """run の実行を Cloud Tasks に依頼し、作成したタスクの `name` を返す。

        タスク名を run_id から決定的に作る（`projects/.../tasks/run-<run_id>`）ことで、同じ
        run_id の二重投入（webhook の再配信）を Cloud Tasks 自身に弾かせる。既に同名タスクが
        あれば `AlreadyExists` になるので、それを捕まえて既存のタスク名をそのまま返す
        （再配信で同じ run を二重実行しない）。
        """
        parent = tasks_v2.CloudTasksAsyncClient.queue_path(self.project, self.location, self.queue)
        name = tasks_v2.CloudTasksAsyncClient.task_path(
            self.project, self.location, self.queue, f"run-{run_id}"
        )
        task = tasks_v2.Task(
            name=name,
            http_request=tasks_v2.HttpRequest(
                url=self.worker_url,
                http_method=tasks_v2.HttpMethod.POST,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"run_id": str(run_id)}).encode("utf-8"),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self.oidc_service_account_email,
                    audience=self.worker_url,
                ),
            ),
            dispatch_deadline=duration_pb2.Duration(seconds=self.dispatch_deadline_s),
        )
        try:
            created = await self.client.create_task(parent=parent, task=task)
        except AlreadyExists:
            return name
        return str(created.name)


__all__ = ["CloudTasksQueue"]
