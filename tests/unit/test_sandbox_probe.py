"""sandbox ロールの疎通確認エンドポイント（S38）。

判定はせず観測できた事実を返すだけの診断口なので、ここで固定するのは
「sandbox ロールにだけ生えること」「CLI が無い環境でも落ちずに事実を返すこと」の 2 点。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from speclino.api.app import create_app
from speclino.sandbox_runner import probe
from speclino.settings import Settings

SELFTEST = "/internal/sandbox/selftest"


async def get(role: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(Settings(role=role)))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["web", "worker", "all"])
async def test_selftest_is_only_on_the_sandbox_role(role: str) -> None:
    assert (await get(role, SELFTEST)).status_code == 404


@pytest.mark.asyncio
async def test_missing_cli_is_reported_as_a_fact_not_an_error() -> None:
    """Cloud Run の外（CLI が無い環境）でも 200 で「無かった」と言う。"""
    res = await get("sandbox", SELFTEST)
    assert res.status_code == 200
    body = res.json()
    assert body["cli_exists"] is False
    assert body["result"] == "cli_not_found"


@pytest.mark.asyncio
async def test_cli_output_is_returned_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI がある環境では終了コードと出力をそのまま返す（成否の解釈はしない）。"""
    monkeypatch.setattr(probe.Path, "is_file", lambda self: True)
    monkeypatch.setattr(probe.os, "access", lambda path, mode: True)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert cmd[0] == str(probe.SANDBOX_CLI)
        assert cmd[1:] == ["do", "--", "/bin/echo", "ok"]
        return subprocess.CompletedProcess(cmd, 0, b"ok\n", b"")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    body = (await get("sandbox", SELFTEST)).json()
    assert body["result"] == "ran"
    assert body["exit_code"] == 0
    assert body["stdout"] == "ok\n"


@pytest.mark.asyncio
async def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe.Path, "is_file", lambda self: True)
    monkeypatch.setattr(probe.os, "access", lambda path, mode: True)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, probe.PROBE_TIMEOUT_S)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert (await get("sandbox", SELFTEST)).json()["result"] == "timeout"


def test_probe_runs_a_fixed_command() -> None:
    """外部入力を受け取らないこと（引数注入の余地が無いこと）を明示的に固定する。"""
    source = Path(probe.__file__).read_text(encoding="utf-8")
    assert '"do", "--", "/bin/echo", "ok"' in source
    assert "request" not in source.lower().replace("requests", "")
