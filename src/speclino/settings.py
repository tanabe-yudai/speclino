"""アプリケーション設定。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数 `SPECLINO_*` から読み込む設定。"""

    model_config = SettingsConfigDict(env_prefix="SPECLINO_")

    env: Literal["local", "prod"] = "local"
    role: Literal["web", "worker", "sandbox", "all"] = "all"
    database_url: str = "postgresql+asyncpg://speclino:speclino@localhost:5432/speclino"
    artifact_dir: Path = Path(".speclino-artifacts")
    public_url: str = "http://localhost:8080"
    # public_url 以外に MCP が受け付けるホスト（以前の公開 URL など。DNS rebinding 対策）
    extra_hosts: list[str] = []

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
    # code_host_kind=local のとき repo_full_name → リポジトリ ID（project の github_repo_id と
    # 照合される。無いリポジトリの project は ID の照合で run が失敗する）
    local_repo_ids: dict[str, int] = {}

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
    # 自分で作れるワークスペースの上限を引き上げる利用者（uid をカンマ区切り）。
    # 複数の審査員が共有するアカウント向け。ほかの利用者は MAX_OWNED_WORKSPACES のまま
    workspace_limit_raised_uids: str = ""
    # LLM 支援の規約（S70）。tier=llm_assisted のルールを worker が評価する（verdict は REVIEW、
    # 確定は人）。無効なら LLM 支援のルールは UNKNOWN。費用は daily_cost_limit_usd の枠を共有する
    llm_policies_enabled: bool = True
    llm_policies_model: str | None = None  # 未指定なら agent_model
    # A2A の議論役（S59）。web に A2A の入口を出すか。本番は Terraform の環境変数で true
    a2a_enabled: bool = False
    a2a_model: str | None = None  # 未指定なら agent_model
    # 対話による知識の作成（S73）。gemini は Vertex AI / Gemini、scripted は開発と E2E 用の台本
    # （ネットワークを使わない決定的な応答）。env=prod では scripted を使えない
    chat_llm: Literal["gemini", "scripted"] = "gemini"
    chat_model: str | None = None  # 未指定なら a2a_model、それも無ければ agent_model
    # 会話の AI の思考の量（Gemini 3 の thinking_level）。思考は出力の上限に含まれる
    chat_thinking_level: Literal["low", "medium", "high"] = "medium"

    # 認証（S13）
    auth_kind: Literal["dev", "identity_platform"] = "dev"
    firebase_project_id: str | None = None

    # 知識の bundle の署名鍵（S62、HMAC-SHA256）。本番は Secret Manager から環境変数へ。
    # 未設定なら env=local は固定の開発鍵、env=prod は起動時にエラー（services/bundle_signing.py）
    bundle_signing_key: str | None = None
    # 鍵の更新の間だけ、前の鍵を検証に使う（#114）。`kid:secret` をカンマ区切り。kid は
    # `speclino signing-key-id` で確かめる。lock の期限（30 日）が過ぎたら外してよい
    bundle_signing_keys_old: str | None = None

    def llm_model_ids(self) -> tuple[str, ...]:
        """設定にある LLM のモデル ID（重複なし）。LLM の出力に漏れていないかの照合に使う。"""
        ids = (
            self.agent_model,
            self.spec_bot_model,
            self.llm_policies_model,
            self.a2a_model,
            self.chat_model,
        )
        return tuple(dict.fromkeys(m for m in ids if m))

    @model_validator(mode="after")
    def _no_local_code_host_in_prod(self) -> Settings:
        # ローカルのコードホストは手元の git をそのまま信じる（GitHub の認証も ID の照合も無い）
        if self.env == "prod" and self.code_host_kind == "local":
            raise ValueError("env=prod では code_host_kind=local を使えない")
        if self.env == "prod" and self.chat_llm == "scripted":
            raise ValueError("env=prod では chat_llm=scripted を使えない")
        return self
