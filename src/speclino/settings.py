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
