"""CloudRunSandbox（S38）: tar 作成・OIDC 認証・junit のファイル名の戻し方の単体試験。

sandbox サービス本体へは接続しない。HTTP は respx で、ID トークン取得は `google.oauth2.id_token`
を差し替えて確かめる（`tests/agent/test_test_run.py` の SandboxResult の組み立て方に合わせる）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from speclino.adapters.sandbox import cloud_run_sandbox as crs
from speclino.adapters.sandbox.cloud_run_sandbox import CloudRunSandbox

SANDBOX_URL = "https://speclino-sandbox-abc123-an.a.run.app"
RUN_URL = f"{SANDBOX_URL}/internal/sandbox/run"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """head と base（宣言済みテストの置き場）を最小限で作る。"""
    head = tmp_path / "head"
    head.mkdir()
    (head / "mod.py").write_text("x = 1\n")
    base = tmp_path / "base"
    tests_dir = base / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_foo.py").write_text("def test_bar():\n    assert True\n")
    return head, base


@pytest.fixture(autouse=True)
def _fake_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crs.google_id_token, "fetch_id_token", lambda request, audience: "fake-id-token"
    )


def _junit_for(flat_test_id: str) -> str:
    flat_file = flat_test_id.split("::", 1)[0]
    name = flat_test_id.split("::", 1)[1]
    classname = flat_file.removesuffix(".py")
    return (
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1">'
        f'<testcase classname="{classname}" name="{name}" file="{flat_file}"/>'
        "</testsuite></testsuites>"
    )


@respx.mock
def test_run_tests_sends_oidc_bearer_and_declared_flat_test_ids(tmp_path: Path) -> None:
    head, base = _fixture(tmp_path)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fake-id-token"
        body = json.loads(request.content)
        assert body["timeout_s"] == 60
        assert len(body["test_ids"]) == 1
        flat = body["test_ids"][0]
        assert flat != "tests/test_foo.py::test_bar"  # 平坦化されている（元 ID のままではない）
        return httpx.Response(
            200,
            json={
                "exit_code": 0,
                "junit_xml": _junit_for(flat),
                "stdout_tail": "1 passed",
                "timed_out": False,
                "runner": "cloud_run_sandbox",
            },
        )

    respx.post(RUN_URL).mock(side_effect=_handler)

    sandbox = CloudRunSandbox(sandbox_url=SANDBOX_URL)
    result = sandbox.run_tests(head, base, ["tests/test_foo.py::test_bar"], timeout_s=60)

    assert result.runner == "cloud_run_sandbox"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.junit_xml is not None
    # 平坦化したファイル名が元のテスト ID に戻っている
    assert 'file="tests/test_foo.py"' in result.junit_xml
    assert 'classname="tests.test_foo"' in result.junit_xml
    assert 'name="test_bar"' in result.junit_xml


@respx.mock
def test_no_test_ids_short_circuits_without_any_http_call(tmp_path: Path) -> None:
    head, base = _fixture(tmp_path)

    result = CloudRunSandbox(sandbox_url=SANDBOX_URL).run_tests(head, base, [], timeout_s=30)

    assert result == crs.SandboxResult(2, None, "テスト ID が無い", False, "cloud_run_sandbox")


@respx.mock
def test_undeclared_test_ids_short_circuit_without_any_http_call(tmp_path: Path) -> None:
    head, base = _fixture(tmp_path)

    result = CloudRunSandbox(sandbox_url=SANDBOX_URL).run_tests(
        head, base, ["tests/does_not_exist.py::test_x"], timeout_s=30
    )

    assert result.exit_code == 2
    assert result.junit_xml is None
    assert result.runner == "cloud_run_sandbox"


@respx.mock
def test_http_error_response_is_reported_without_raising(tmp_path: Path) -> None:
    head, base = _fixture(tmp_path)
    respx.post(RUN_URL).mock(return_value=httpx.Response(503, json={"detail": "unavailable"}))

    result = CloudRunSandbox(sandbox_url=SANDBOX_URL).run_tests(
        head, base, ["tests/test_foo.py::test_bar"], timeout_s=30
    )

    assert result.exit_code == -1
    assert result.junit_xml is None
    assert result.timed_out is False
    assert result.runner == "cloud_run_sandbox"


@respx.mock
def test_timeout_is_reported_as_timed_out(tmp_path: Path) -> None:
    head, base = _fixture(tmp_path)
    respx.post(RUN_URL).mock(side_effect=httpx.TimeoutException("boom"))

    result = CloudRunSandbox(sandbox_url=SANDBOX_URL).run_tests(
        head, base, ["tests/test_foo.py::test_bar"], timeout_s=30
    )

    assert result.exit_code == -1
    assert result.timed_out is True
    assert result.runner == "cloud_run_sandbox"
