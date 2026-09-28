"""web から worker の `POST /internal/mcp/context` を呼ぶ（S54）。

秘密鍵を持つのは worker だけなので、web の MCP `context_get` はここ経由で worker に組み立てを
任せる。認証は Cloud Run の IAM（worker の run.invoker は tasks SA と web SA だけ）で、web は
ADC から audience = worker の URL の ID トークンを取る（`adapters/sandbox/cloud_run_sandbox.py`
と同じ方式）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from speclino.services.mcp_context import ContextUnavailable

CONTEXT_PATH = "/internal/mcp/context"


class WorkerContextClient:
    def __init__(
        self,
        worker_url: str,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport  # テストで差し替える

    async def get(self, project_id: uuid.UUID, workspace: str, ref: str) -> dict[str, Any]:
        token = await asyncio.to_thread(self._id_token)
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as http:
            response = await http.post(
                f"{self.worker_url}{CONTEXT_PATH}",
                json={"project_id": str(project_id), "workspace": workspace, "ref": ref},
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code == 409:
            # worker は ContextUnavailable を ErrorResponse（{"error": {"code", "message"}}）で返す
            error = response.json().get("error") or {}
            raise ContextUnavailable(str(error.get("message", "context を返せない")))
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def _id_token(self) -> str:
        token: str = google_id_token.fetch_id_token(  # type: ignore[no-untyped-call]
            GoogleAuthRequest(), self.worker_url
        )
        return token


__all__ = ["CONTEXT_PATH", "WorkerContextClient"]
