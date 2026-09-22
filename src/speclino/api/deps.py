"""アプリ全体の依存（DB、コードホスト、キュー、成果物、サービス）を役割に応じて組み立てる。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.adapters.github.app_auth import GitHubAppAuth
from speclino.adapters.github.client import GitHubClient
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.agent.permissions import AgentPermissions
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.db.session import make_engine, make_session_factory
from speclino.ports.blob_store import BlobStore
from speclino.ports.code_host import CodeHost
from speclino.ports.task_queue import TaskQueue
from speclino.services.cost_guard import CostGuard, CostLedger
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    code_host: CodeHost
    queue: TaskQueue
    blobs: BlobStore
    service: ReviewRunService


class _SessionLedger:
    """CostLedger を session_factory 越しに提供する（worker 用）。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def add(self, day, run_id, estimated_usd, input_tokens, output_tokens, model):  # type: ignore[no-untyped-def]
        import uuid

        async with self.factory() as s, s.begin():
            await CostLedgerRepository(s).add(
                day=day,
                run_id=uuid.UUID(run_id) if run_id else None,
                estimated_usd=estimated_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
            )

    async def total_for_day(self, day):  # type: ignore[no-untyped-def]
        async with self.factory() as s:
            return await CostLedgerRepository(s).total_for_day(day)


def build_code_host(settings: Settings, installation_id: int | None = None) -> CodeHost:
    if settings.code_host_kind == "local":
        return LocalCodeHost(repos={k: Path(v) for k, v in settings.local_repos.items()})
    pem = settings.github_private_key
    if pem is None and settings.github_private_key_path is not None:
        pem = settings.github_private_key_path.read_text(encoding="utf-8")
    if settings.github_app_id is None or pem is None:
        raise RuntimeError("GitHub App の設定（SPECLINO_GITHUB_APP_ID / PRIVATE_KEY）が無い")
    auth = GitHubAppAuth(settings.github_app_id, pem)
    return GitHubClient(
        auth,
        installation_id or 0,
        httpx.AsyncClient(timeout=30.0),
        base_url=settings.github_api_base,
    )


def build_state(settings: Settings) -> AppState:
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    blobs: BlobStore = FileSystemBlobStore(settings.artifact_dir)
    code_host = build_code_host(settings)
    holder: dict[str, ReviewRunService] = {}
    if settings.queue_kind == "inprocess":
        queue: TaskQueue = InProcessQueue(
            lambda run_id: holder["svc"].execute(run_id), immediate=False
        )
    else:  # pragma: no cover - S26 で Cloud Tasks を実装
        raise RuntimeError("cloud_tasks キューは未実装（S26）")
    ledger: CostLedger = _SessionLedger(factory)
    service = ReviewRunService(
        session_factory=factory,
        code_host=code_host,
        queue=queue,
        blobs=blobs,
        config=ReviewRunConfig(
            permissions=AgentPermissions(),
            model=settings.agent_model,
            details_url_base=f"{settings.public_url.rstrip('/')}/runs",
            agent_enabled=settings.agent_enabled,
        ),
        cost_guard=CostGuard(ledger, Decimal(str(settings.daily_cost_limit_usd))),
    )
    holder["svc"] = service
    return AppState(settings, engine, factory, code_host, queue, blobs, service)


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.speclino
    return state
