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

    # エージェント
    agent_enabled: bool = True
    agent_model: str = "gemini-3.8-flash"
    daily_cost_limit_usd: float = 3.0
    approvers: list[str] = []  # プロジェクト設定に無いときの既定の承認者
    demo_rerun_limit_per_day: int = 20

    # 認証（S13）
    auth_kind: Literal["dev", "identity_platform"] = "dev"
    firebase_project_id: str | None = None
    allowed_users: list[str] = []
