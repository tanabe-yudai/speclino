"""`speclino sync`（S68）: 取得 → 書き出し → lock。サーバは httpx.MockTransport で代える。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from speclino import cli, cli_sync
from speclino.domain.knowledge_sync import SyncPayload
from speclino.services import knowledge_sync as ks
from tests.unit.services.test_knowledge_sync import BASE, NOW, _payload

TOKEN = "slsync_secret-token-value"
runner = CliRunner()


def _body(files: dict[str, str] | None = None) -> bytes:
    f, lock, bundle = _payload(BASE)
    payload = SyncPayload(bundle=bundle, files=f if files is None else files, lock=lock)
    return payload.model_dump_json().encode()


@pytest.fixture
def server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[int, bytes], list[httpx.Request]]]:
    def install(status: int, body: bytes) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, content=body)

        monkeypatch.setattr(cli_sync, "transport_factory", lambda: httpx.MockTransport(handler))
        return seen

    monkeypatch.setenv(cli_sync.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(cli_sync.URL_ENV, "http://speclino.test")
    yield install


def test_sync_writes_bundle(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]]
) -> None:
    seen = server(200, _body())
    result = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path), "--repo-id", "42"])
    assert result.exit_code == 0, result.output
    assert "bundle 1" in result.output
    assert TOKEN not in result.output
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].url.params["repo_id"] == "42"
    assert (tmp_path / ".claude/skills/review/SKILL.md").exists()
    lock_json = (tmp_path / ks.LOCK_PATH).read_text()
    _, _, bundle = _payload(BASE)
    finding = ks.check_lock(
        tmp_path,
        lock_json,
        current=bundle,
        current_members=bundle.members,
        github_repo_id=42,
        now=NOW,
    )
    assert finding.verdict == "VERIFIED_IN_SCOPE"
    # 2 回目はリポジトリ ID を lock から読む
    again = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path)])
    assert again.exit_code == 0, again.output


def test_sync_requires_token_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli_sync.TOKEN_ENV, raising=False)
    result = runner.invoke(
        cli.app, ["sync", "--repo", str(tmp_path), "--repo-id", "42", "--url", "http://x"]
    )
    assert result.exit_code == 2
    assert cli_sync.TOKEN_ENV in result.output


def test_sync_needs_repo_id_first_time(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]]
) -> None:
    server(200, _body())
    result = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path)])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("status", "word"), [(401, "トークン"), (404, "対象ではない"), (500, "HTTP 500")]
)
def test_sync_server_errors_keep_state(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]], status: int, word: str
) -> None:
    server(status, json.dumps({"error": {"code": "x", "message": TOKEN}}).encode())
    result = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path), "--repo-id", "42"])
    assert result.exit_code == 1
    assert word in result.output and TOKEN not in result.output
    assert list(tmp_path.iterdir()) == []


def test_sync_rejects_unsafe_payload(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]]
) -> None:
    files, lock, bundle = _payload(BASE)
    evil = {**files, "../escape.md": "x"}
    lock = lock.model_copy(update={"files": {**lock.files, "../escape.md": ks.file_hash("x")}})
    server(200, SyncPayload(bundle=bundle, files=evil, lock=lock).model_dump_json().encode())
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(cli.app, ["sync", "--repo", str(repo), "--repo-id", "42"])
    assert result.exit_code == 1
    assert "前のまま" in result.output
    assert not (tmp_path / "escape.md").exists()
    assert list(repo.iterdir()) == []


def test_sync_rejects_lock_for_other_repo(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]]
) -> None:
    server(200, _body())
    result = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path), "--repo-id", "7"])
    assert result.exit_code == 1
    assert not (tmp_path / ks.LOCK_PATH).exists()


def test_sync_warns_about_kept_user_files(
    tmp_path: Path, server: Callable[[int, bytes], list[httpx.Request]]
) -> None:
    (tmp_path / ".speclino/specs").mkdir(parents=True)
    (tmp_path / ".speclino/specs/user.md").write_text("user spec")
    _, lock, _ = _payload(BASE)
    forged = lock.model_copy(
        update={"files": {".speclino/specs/user.md": ks.file_hash("y")}, "signature": "forged"}
    )
    (tmp_path / ks.LOCK_PATH).write_text(forged.model_dump_json())
    server(200, _body())
    result = runner.invoke(cli.app, ["sync", "--repo", str(tmp_path), "--repo-id", "42"])
    assert result.exit_code == 0, result.output
    assert "警告: .speclino/specs/user.md" in result.output
    assert (tmp_path / ".speclino/specs/user.md").read_text() == "user spec"
