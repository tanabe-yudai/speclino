"""ローカルのコードホスト（code_host_kind=local）に、設定のリポジトリ ID が渡る。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from speclino.api.deps import build_code_host
from speclino.settings import Settings


def test_local_code_host_gets_repo_ids(tmp_path: Path) -> None:
    settings = Settings(
        code_host_kind="local",
        local_repos={"example/shop": tmp_path},
        local_repo_ids={"example/shop": 7},
    )
    host = build_code_host(settings)
    assert asyncio.run(host.repository_id("example/shop")) == 7
    assert asyncio.run(host.repository_id("example/other")) is None
