"""アプリ全体の依存（DB、コードホスト、キュー、成果物、サービス）を役割に応じて組み立てる。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.adapters.github.app_auth import GitHubAppAuth
from speclino.adapters.github.client import GitHubClient
from speclino.adapters.github.user_oauth import GitHubUserOAuth
from speclino.adapters.llm.gemini_json import GeminiJsonLlm
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.cloud_tasks import CloudTasksQueue
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.sandbox.cloud_run_sandbox import CloudRunSandbox
from speclino.adapters.sandbox.disabled import DisabledSandbox
from speclino.adapters.sandbox.docker_local import DockerSandbox
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.adapters.storage.gcs import GcsBlobStore
from speclino.agent.permissions import AgentPermissions
from speclino.db.repositories.cost_ledger import CostLedgerRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.session import make_engine, make_session_factory
from speclino.domain.policy import PolicyRevision
from speclino.ports.blob_store import BlobStore
from speclino.ports.code_host import CodeHost
from speclino.ports.mcp_context import McpContextSource
from speclino.ports.sandbox import Sandbox
from speclino.ports.task_queue import TaskQueue
from speclino.ports.text_llm import TextLlm
from speclino.services import bundle_signing
from speclino.services.cost_guard import CostGuard, CostLedger
from speclino.services.knowledge_inputs import make_knowledge_resolver
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings

if TYPE_CHECKING:
    from speclino.a2a_server.executor import SkillHandler
    from speclino.domain.a2a import SkillId


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    code_host: CodeHost
    queue: TaskQueue
    blobs: BlobStore
    service: ReviewRunService
    # MCP context_get の取得元。None ならプロセス内で組み立てる（ローカル、worker）。
    # web ロールの本番は秘密鍵を持つ worker に委譲する（S54、`services/mcp_context.py`）
    context_source: McpContextSource | None = None
    # GitHub App のユーザー認可（S55 のリポジトリ登録）。client_id / secret が無ければ None
    github_user_oauth: GitHubUserOAuth | None = None
    # A2A のスキルの処理（S59）。None なら state から本物を組み立てる。テストで差し替える
    a2a_handlers: dict[SkillId, SkillHandler] | None = None
    # AI の書き換え提案（S67）の LLM。None なら初回に Gemini を作る。テストで台本に差し替える
    spec_suggest_llm: TextLlm | None = None


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


def make_team_policy_resolver(
    factory: async_sessionmaker[AsyncSession],
) -> Callable[[str], Awaitable[PolicyRevision | None]]:
    """チームの active な規約改訂を DB から引く関数を作る（S42）。

    `services/check_pipeline.py` は DB に依存させない（CLI が DB 無しで動く必要がある）ため、
    規約はデータとして渡す。DB から取るのはここ＝配線の層の仕事。
    """

    async def resolve(team: str) -> PolicyRevision | None:
        async with factory() as session:
            return await PolicyRevisionRepository(session).active_revision(team)

    return resolve


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


def build_sandbox(settings: Settings) -> Sandbox:
    if settings.sandbox_kind == "docker":
        return DockerSandbox()
    if settings.sandbox_kind == "cloud_run":
        if not settings.sandbox_url:
            raise RuntimeError("cloud_run サンドボックスの設定が無い（SPECLINO_SANDBOX_URL）")
        return CloudRunSandbox(sandbox_url=settings.sandbox_url)
    return DisabledSandbox()


class LazyCodeHost:
    """初回利用時にだけ実体を作る CodeHost。

    web ロールは run を作って enqueue するだけで、Check の更新とスナップショット取得は
    worker が行う。起動時に実体を作ると web にも GitHub App の秘密鍵が要ることになり、
    「秘密鍵は worker だけが持つ」構成（design のサービス構成表）を取れない。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._inner: CodeHost | None = None

    @property
    def inner(self) -> CodeHost:
        if self._inner is None:
            self._inner = build_code_host(self._settings)
        return self._inner

    async def create_check(
        self, repo: str, head_sha: str, name: str, details_url: str | None, external_id: str | None
    ) -> int:
        return await self.inner.create_check(repo, head_sha, name, details_url, external_id)

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
        await self.inner.update_check(
            repo, check_run_id, status, conclusion, title, summary, text, annotations
        )

    async def post_comment(self, repo: str, issue_number: int, body: str) -> int:
        return await self.inner.post_comment(repo, issue_number, body)

    async def upsert_comment(self, repo: str, issue_number: int, marker: str, body: str) -> int:
        return await self.inner.upsert_comment(repo, issue_number, marker, body)

    async def fetch_snapshot(self, repo: str, sha: str, dest_dir: Path) -> Path:
        return await self.inner.fetch_snapshot(repo, sha, dest_dir)

    async def repository_id(self, repo: str) -> int | None:
        return await self.inner.repository_id(repo)

    async def branch_head(self, repo: str, branch: str) -> str | None:
        return await self.inner.branch_head(repo, branch)


def build_state(settings: Settings) -> AppState:
    if settings.role in ("web", "worker", "all"):
        # bundle を作る（web の承認）・確かめる（worker の run）役割は署名鍵が要る。本番で
        # 未設定なら最初の承認で落ちる前に、起動で落とす
        bundle_signing.configure(settings)
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    if settings.blob_kind == "fs":
        blobs: BlobStore = FileSystemBlobStore(settings.artifact_dir)
    else:
        bucket = settings.gcs_bucket
        if not bucket:
            raise RuntimeError("gcs バケットの設定が無い（SPECLINO_GCS_BUCKET）")
        blobs = GcsBlobStore(bucket)
    code_host: CodeHost = LazyCodeHost(settings)
    holder: dict[str, ReviewRunService] = {}
    if settings.queue_kind == "inprocess":
        queue: TaskQueue = InProcessQueue(
            lambda run_id: holder["svc"].execute(run_id), immediate=False
        )
    else:
        project = settings.cloud_tasks_project
        location = settings.cloud_tasks_location
        queue_name = settings.cloud_tasks_queue
        worker_url = settings.cloud_tasks_worker_url
        oidc_email = settings.cloud_tasks_oidc_service_account_email
        missing = [
            name
            for name, value in (
                ("SPECLINO_CLOUD_TASKS_PROJECT", project),
                ("SPECLINO_CLOUD_TASKS_LOCATION", location),
                ("SPECLINO_CLOUD_TASKS_QUEUE", queue_name),
                ("SPECLINO_CLOUD_TASKS_WORKER_URL", worker_url),
                ("SPECLINO_CLOUD_TASKS_OIDC_SERVICE_ACCOUNT_EMAIL", oidc_email),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"cloud_tasks キューの設定が無い: {', '.join(missing)}")
        assert project and location and queue_name and worker_url and oidc_email
        queue = CloudTasksQueue(
            project=project,
            location=location,
            queue=queue_name,
            worker_url=worker_url,
            oidc_service_account_email=oidc_email,
            dispatch_deadline_s=settings.cloud_tasks_dispatch_deadline_s,
        )
    ledger: CostLedger = _SessionLedger(factory)
    service = ReviewRunService(
        session_factory=factory,
        code_host=code_host,
        queue=queue,
        blobs=blobs,
        config=ReviewRunConfig(
            # 隔離実行の当てが無いのに test_run を許可しても sandbox_unavailable を返すだけなので、
            # サンドボックスが設定されているときだけ許可する（S38）。
            permissions=AgentPermissions(test_run_enabled=settings.sandbox_kind != "disabled"),
            model=settings.agent_model,
            details_url_base=f"{settings.public_url.rstrip('/')}/runs",
            agent_enabled=settings.agent_enabled,
            sandbox=build_sandbox(settings),
            resolve_team_policy=make_team_policy_resolver(factory),
            # S65: ワークスペースに登録されたプロジェクトの run は bundle を固定して検査する
            resolve_knowledge=make_knowledge_resolver(factory),
            # Gemini のクライアントは初回呼出しで作る（web ロールや認証情報の無い環境でも
            # 起動できる。認証が無ければ bot は理由をコメントに書く）
            spec_llm=(
                GeminiJsonLlm(settings.spec_bot_model or settings.agent_model)
                if settings.spec_bot_enabled
                else None
            ),
        ),
        cost_guard=CostGuard(ledger, Decimal(str(settings.daily_cost_limit_usd))),
    )
    holder["svc"] = service
    context_source: McpContextSource | None = None
    if settings.role == "web" and settings.cloud_tasks_worker_url:
        from speclino.adapters.worker.context_client import WorkerContextClient

        context_source = WorkerContextClient(settings.cloud_tasks_worker_url)
    user_oauth = (
        GitHubUserOAuth(settings.github_client_id, settings.github_client_secret)
        if settings.github_client_id and settings.github_client_secret
        else None
    )
    return AppState(
        settings, engine, factory, code_host, queue, blobs, service, context_source, user_oauth
    )


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.speclino
    return state
