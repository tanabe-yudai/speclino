"""S75 fix round 2: アクセスログの使い捨ての取得トークンを伏せる。"""

from __future__ import annotations

import logging
import secrets

import pytest

from speclino.api import access_log
from speclino.api.app import create_app
from speclino.settings import Settings

# ダミーの値は実行時に作る（固定の文字列は秘密の検出に掛かる）
TOKEN = "slplan_" + secrets.token_urlsafe(32)


def test_uvicorn_access_log_masks_plan_token(caplog: pytest.LogCaptureFixture) -> None:
    create_app(Settings(env="local", role="all", code_host_kind="local"))
    create_app(Settings(env="local", role="all", code_host_kind="local"))
    logger = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, access_log.RedactPlanTokens) for f in logger.filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            f"/api/sync/plans/{TOKEN}",
            "1.1",
            200,
        )
        logger.info(f"raw /api/sync/plans/{TOKEN}")
    text = caplog.text
    assert TOKEN not in text
    assert text.count("/api/sync/plans/slplan_***") == 2


def test_redact_leaves_other_text() -> None:
    assert access_log.redact("GET /api/runs/abc") == "GET /api/runs/abc"
