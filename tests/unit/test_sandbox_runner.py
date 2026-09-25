"""sandbox ロールの本実装（S38）: `POST /internal/sandbox/run` の単体試験。

生きた sandbox CLI は無い前提で、`SANDBOX_CLI` と `subprocess.Popen` / `subprocess.run` を
差し替えて確かめる。見るのは大きく 2 群:

- 元からの契約: tar の展開が path traversal を拒否すること／宣言済み以外のテスト ID を
  pytest に渡さないこと／タイムアウト時に `sandbox delete --force <name>` を呼ぶこと。
- codex レビューで指摘された堅牢化 4 件: junit.xml のシンボリックリンクを追わないこと／
  標準出力の保持量に上限があること／tar の展開量に上限があること／タイムアウト時の
  delete が失敗したら握りつぶさず報告すること。
"""

from __future__ import annotations

import base64
import io
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from speclino.api.app import create_app
from speclino.sandbox_runner import runner
from speclino.settings import Settings

RUN = "/internal/sandbox/run"


async def post(body: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(Settings(role="sandbox")))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(RUN, json=body)


def _tar_b64(members: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _valid_tar_b64() -> str:
    return _tar_b64(
        {
            "head/mod.py": b"x = 1\n",
            "tests/test_probe.py": b"def test_ok():\n    assert True\n",
            "tests/pytest.ini": b"[pytest]\naddopts = -p no:cacheprovider\n",
        }
    )


def _mount_src(cmd: list[str], dst: str) -> Path:
    """cmd 中の `--mount type=bind,src=...,dst=<dst>` からホスト側パスを取り出す（テスト用）。"""
    suffix = f",dst={dst}"
    for arg in cmd:
        if arg.startswith("type=bind,src=") and arg.endswith(suffix):
            return Path(arg[len("type=bind,src=") : -len(suffix)])
    raise AssertionError(f"mount for {dst} not found in {cmd}")


class _FakePopen:
    """`subprocess.Popen` の代替。`stdout` は事前に用意したバイト列を返す `BytesIO`。

    `timeout_then_code` を渡すと、最初の `wait()` は `TimeoutExpired` を送出し、
    （`_run_capped` が `kill()` した後に呼ぶ）2 回目の `wait()` はその値を返す
    （kill 後にプロセスが reap できた状態を模す）。
    """

    def __init__(
        self,
        cmd: list[str],
        output: bytes,
        *,
        code: int = 0,
        timeout_then_code: int | None = None,
    ) -> None:
        self.args = cmd
        self.stdout = io.BytesIO(output)
        self.killed = False
        self._code = code
        self._timeout_then_code = timeout_then_code
        self._waited_once = False

    def wait(self, timeout: float | None = None) -> int:
        if self._timeout_then_code is not None and not self._waited_once:
            self._waited_once = True
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return self._timeout_then_code if self._timeout_then_code is not None else self._code

    def kill(self) -> None:
        self.killed = True


@pytest.fixture(autouse=True)
def _fake_sandbox_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI の実在チェックだけを満たす（呼び出し自体は各テストで subprocess を差し替える）。"""
    cli = tmp_path / "sandbox"
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setattr(runner, "SANDBOX_CLI", cli)


@pytest.mark.asyncio
async def test_path_traversal_member_is_rejected() -> None:
    """ ".." で目的地の外に出ようとするメンバーは tarfile の filter="data" が拒否する。"""
    body = {
        "tar_b64": _tar_b64({"../evil.txt": b"pwned"}),
        "test_ids": ["test_probe.py::test_ok"],
        "timeout_s": 30,
    }
    res = await post(body)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_tar"


@pytest.mark.asyncio
async def test_absolute_path_member_does_not_escape_and_yields_invalid_layout() -> None:
    """絶対パスのメンバーは展開先の外に出ない（filter="data" が相対化する）。

    そのため head/ tests/ が作られず、"tar の中身が期待する形でない" として拒否される。
    """
    body = {
        "tar_b64": _tar_b64({"/etc/passwd": b"pwned"}),
        "test_ids": ["test_probe.py::test_ok"],
        "timeout_s": 30,
    }
    res = await post(body)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_layout"


@pytest.mark.asyncio
async def test_undeclared_and_missing_test_ids_are_dropped_before_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tar に実在しない・パスが不正なテスト ID は、宣言済みとして扱わずコマンドに含めない。"""
    captured: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        captured.append(cmd)
        return _FakePopen(cmd, b"1 passed", code=0)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    body = {
        "tar_b64": _valid_tar_b64(),
        "test_ids": [
            "test_probe.py::test_ok",  # 宣言済み（tar に実在）
            "../../etc/passwd::test_evil",  # 絶対パス化を試みる不正な ID
            "does_not_exist.py::test_x",  # tar に無い
        ],
        "timeout_s": 30,
    }
    res = await post(body)
    assert res.status_code == 200
    assert len(captured) == 1  # sandbox delete は呼ばれていない（タイムアウトしていない）
    cmd = captured[0]
    assert cmd.count("/work/tests/test_probe.py::test_ok") == 1
    assert not any("passwd" in a or "does_not_exist" in a for a in cmd)


@pytest.mark.asyncio
async def test_no_declared_test_ids_short_circuits_without_running_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda cmd, **kw: calls.append(cmd) or _FakePopen(cmd, b""),
    )
    body = {
        "tar_b64": _valid_tar_b64(),
        "test_ids": ["does_not_exist.py::test_x"],
        "timeout_s": 30,
    }
    res = await post(body)
    assert res.status_code == 200
    data = res.json()
    assert data["exit_code"] == 2 and data["junit_xml"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_timeout_deletes_the_sandbox_by_its_deterministic_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []
    delete_calls: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        popen_calls.append(cmd)
        return _FakePopen(cmd, b"", timeout_then_code=0)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        delete_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    body = {
        "tar_b64": _valid_tar_b64(),
        "test_ids": ["test_probe.py::test_ok"],
        "timeout_s": 1,
    }
    res = await post(body)
    assert res.status_code == 200
    data = res.json()
    assert data["timed_out"] is True
    assert data["exit_code"] == -1
    assert len(popen_calls) == 1
    assert len(delete_calls) == 1
    do_cmd = popen_calls[0]
    sandbox_name = do_cmd[do_cmd.index("--sandbox-name") + 1]
    assert delete_calls[0][1:] == ["delete", "--force", sandbox_name]


@pytest.mark.asyncio
async def test_missing_cli_is_a_503_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "SANDBOX_CLI", Path("/nonexistent/sandbox"))
    res = await post(
        {"tar_b64": _valid_tar_b64(), "test_ids": ["test_probe.py::test_ok"], "timeout_s": 30}
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "sandbox_cli_not_found"


# --- 堅牢化 1: junit.xml のシンボリックリンクを追わない ---------------------------------


def test_read_junit_safely_rejects_symlink(tmp_path: Path) -> None:
    """未信頼コードが junit.xml をホスト側の別ファイルへのシンボリックリンクに差し替えても、
    O_NOFOLLOW で開くため追わない。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("secret-env-value")
    link = tmp_path / "junit.xml"
    link.symlink_to(secret)

    content, note = runner._read_junit_safely(link)
    assert content is None
    assert note is not None and "シンボリックリンク" in note


def test_read_junit_safely_rejects_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "JUNIT_MAX_BYTES", 10)
    big = tmp_path / "junit.xml"
    big.write_text("x" * 100)

    content, note = runner._read_junit_safely(big)
    assert content is None
    assert note is not None and "大きすぎる" in note


def test_read_junit_safely_returns_none_quietly_when_missing(tmp_path: Path) -> None:
    """ファイルが単に無い（pytest が起動すら出来なかった等）のは異常ではない。"""
    content, note = runner._read_junit_safely(tmp_path / "does_not_exist.xml")
    assert content is None
    assert note is None


def test_read_junit_safely_reads_a_regular_file(tmp_path: Path) -> None:
    f = tmp_path / "junit.xml"
    f.write_text("<testsuite></testsuite>")

    content, note = runner._read_junit_safely(f)
    assert content == "<testsuite></testsuite>"
    assert note is None


@pytest.mark.asyncio
async def test_junit_symlink_is_not_followed_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """実行中に out/junit.xml がホスト側の秘密ファイルへのシンボリックリンクに
    差し替わっても、レスポンスにその中身が漏れない。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("super-secret-value")

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        out_dir = _mount_src(cmd, "/work/out")
        (out_dir / "junit.xml").symlink_to(secret)
        return _FakePopen(cmd, b"1 passed", code=0)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    res = await post(
        {"tar_b64": _valid_tar_b64(), "test_ids": ["test_probe.py::test_ok"], "timeout_s": 30}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["junit_xml"] is None
    assert "シンボリックリンク" in data["stdout_tail"]
    assert "super-secret-value" not in data["stdout_tail"]


# --- 堅牢化 2: 標準出力の保持量に上限を設ける ------------------------------------------


def test_run_capped_keeps_only_the_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限を超える出力は読み捨てられ、末尾だけがメモリに残る。"""
    monkeypatch.setattr(runner, "STDOUT_CAP_BYTES", 10)
    big_output = b"0123456789" * 1000  # 10000 bytes、上限よりずっと大きい

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        return _FakePopen(cmd, big_output, code=0)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    stdout, code = runner._run_capped(["dummy"], 30)
    assert code == 0
    assert len(stdout) <= 10
    assert stdout == big_output[-10:]


@pytest.mark.asyncio
async def test_large_stdout_does_not_crash_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限より大きい出力でもリクエストは正常に完了し、末尾だけが返る（メモリに全量は残さない）。"""
    monkeypatch.setattr(runner, "STDOUT_CAP_BYTES", 100)
    huge = b"A" * (5 * 1024 * 1024)  # 5 MiB

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        return _FakePopen(cmd, huge, code=0)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    res = await post(
        {"tar_b64": _valid_tar_b64(), "test_ids": ["test_probe.py::test_ok"], "timeout_s": 30}
    )
    assert res.status_code == 200
    assert len(res.json()["stdout_tail"]) <= 2000


# --- 堅牢化 3: tar の展開量に上限を設ける ----------------------------------------------


@pytest.mark.asyncio
async def test_tar_with_too_many_members_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TAR_MAX_MEMBERS", 3)
    tar_b64 = _tar_b64({f"head/f{i}.py": b"x" for i in range(5)})
    res = await post({"tar_b64": tar_b64, "test_ids": [], "timeout_s": 30})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "tar_too_many_members"


@pytest.mark.asyncio
async def test_tar_member_too_large_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TAR_MAX_MEMBER_BYTES", 10)
    tar_b64 = _tar_b64({"head/big.py": b"x" * 100})
    res = await post({"tar_b64": tar_b64, "test_ids": [], "timeout_s": 30})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "tar_member_too_large"


@pytest.mark.asyncio
async def test_tar_total_size_too_large_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TAR_MAX_MEMBER_BYTES", 1000)
    monkeypatch.setattr(runner, "TAR_MAX_TOTAL_BYTES", 50)
    tar_b64 = _tar_b64({"head/a.py": b"x" * 30, "head/b.py": b"x" * 30})
    res = await post({"tar_b64": tar_b64, "test_ids": [], "timeout_s": 30})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "tar_too_large"


@pytest.mark.asyncio
async def test_tar_within_limits_is_still_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限の検査自体が正常系を壊していないことを確かめる（小さい tar は素通りする）。"""
    monkeypatch.setattr(runner, "TAR_MAX_MEMBERS", 3)
    monkeypatch.setattr(runner, "TAR_MAX_MEMBER_BYTES", 1000)
    monkeypatch.setattr(runner, "TAR_MAX_TOTAL_BYTES", 1000)
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, b"1 passed", code=0)
    )
    res = await post(
        {"tar_b64": _valid_tar_b64(), "test_ids": ["test_probe.py::test_ok"], "timeout_s": 30}
    )
    assert res.status_code == 200


# --- 堅牢化 4: タイムアウト時の削除を確実にする ----------------------------------------


def test_delete_sandbox_returns_none_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""),
    )
    assert runner._delete_sandbox("speclino-sbx-x") is None


def test_delete_sandbox_reports_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"boom"),
    )
    reason = runner._delete_sandbox("speclino-sbx-x")
    assert reason is not None
    assert "exit=1" in reason


def test_delete_sandbox_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    reason = runner._delete_sandbox("speclino-sbx-x")
    assert reason is not None
    assert "タイムアウト" in reason


@pytest.mark.asyncio
async def test_timeout_with_failed_delete_reports_it_in_stdout_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete --force が失敗しても、成功したように見せず stdout_tail にその事実を残す。"""

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        return _FakePopen(cmd, b"", timeout_then_code=0)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"cannot delete")

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    res = await post(
        {"tar_b64": _valid_tar_b64(), "test_ids": ["test_probe.py::test_ok"], "timeout_s": 1}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["timed_out"] is True
    assert "delete" in data["stdout_tail"]
    assert "exit=1" in data["stdout_tail"]
