"""アプリケーション設定。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数 `SPECLINO_*` から読み込む設定。"""

    model_config = SettingsConfigDict(env_prefix="SPECLINO_")

    env: Literal["local", "prod"] = "local"
    role: Literal["web", "worker", "sandbox", "all"] = "all"
    database_url: str = "postgresql+asyncpg://speclino:speclino@localhost:5432/speclino"
    artifact_dir: Path = Path(".speclino-artifacts")
    public_url: str = "http://localhost:8080"

    # GitHub App（worker が鍵を持つ。web は Webhook 秘密だけ）
    github_app_id: int | None = None
    # GitHub App のユーザー認可（S55 のリポジトリ登録）。web だけが使う
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_private_key: str | None = None  # PEM 本文（Secret Manager から環境変数へ）
    github_private_key_path: Path | None = None
    github_webhook_secret: str | None = None
    github_api_base: str = "https://api.github.com"

    # 実行
    queue_kind: Literal["inprocess", "cloud_tasks"] = "inprocess"
    blob_kind: Literal["fs", "gcs"] = "fs"
    gcs_bucket: str | None = None
    code_host_kind: Literal["github", "local"] = "github"
    local_repos: dict[str, Path] = {}  # code_host_kind=local のとき repo_full_name → パス

    # サンドボックス（test_run、S31/S38）: docker はローカル、cloud_run は本番の Cloud Run sandboxes
    sandbox_kind: Literal["disabled", "docker", "cloud_run"] = "disabled"
    sandbox_url: str = ""  # sandbox_kind=cloud_run のとき sandbox ロールのサービス URL

    # Cloud Tasks（S26）: queue_kind=cloud_tasks で使う。未設定だと build_state が落ちる
    cloud_tasks_project: str | None = None
    cloud_tasks_location: str | None = None
    cloud_tasks_queue: str | None = None
    cloud_tasks_worker_url: str | None = None  # worker の URL（内部で /internal/tasks/run を付与）
    cloud_tasks_oidc_service_account_email: str | None = None
    cloud_tasks_dispatch_deadline_s: int = 1500  # 根拠: infra/envs/prod/tasks.tf 冒頭コメント

    # エージェント
    agent_enabled: bool = True
    agent_model: str = "gemini-3.8-flash"
    daily_cost_limit_usd: float = 3.0
    # 仕様 PR bot（S46）: 仕様 Markdown を変える PR に Spec IR の候補をコメントする。
    # Gemini の認証はエージェントと同じ（GOOGLE_GENAI_USE_VERTEXAI + ADC 等）。費用は
    # daily_cost_limit_usd の枠を共有する。モデル未指定なら agent_model を使う
    spec_bot_enabled: bool = True
    spec_bot_model: str | None = None
    demo_rerun_limit_per_day: int = 20
    # LLM 支援の規約（S70）。tier=llm_assisted のルールを worker が評価する（verdict は REVIEW、
    # 確定は人）。無効なら LLM 支援のルールは UNKNOWN。費用は daily_cost_limit_usd の枠を共有する
    llm_policies_enabled: bool = True
    llm_policies_model: str | None = None  # 未指定なら agent_model
    # A2A の議論役（S59）。web に A2A の入口を出すか。本番は Terraform の環境変数で true
    a2a_enabled: bool = False
    a2a_model: str | None = None  # 未指定なら agent_model

    # 認証（S13）
    auth_kind: Literal["dev", "identity_platform"] = "dev"
    firebase_project_id: str | None = None

    # 知識の bundle の署名鍵（S62、HMAC-SHA256）。本番は Secret Manager から環境変数へ。
    # 未設定なら env=local は固定の開発鍵、env=prod は起動時にエラー（services/bundle_signing.py）
    bundle_signing_key: str | None = None
