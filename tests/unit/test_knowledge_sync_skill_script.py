"""S78: 手順書 SKILL.md に載せた「実行スクリプトの取得 → sha256 の確認 → 実行」の Python を、
書かれているとおりに実行して確かめる（ローカルの http サーバから配る）。"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins" / "speclino" / "skills" / "speclino-knowledge-sync" / "SKILL.md"

# 配られる実行スクリプトの代わり。受け取った引数を出力し、終了コード 7 で終わる
FAKE_SCRIPT = b"import sys\nprint('ran', *sys.argv[1:])\nsys.exit(7)\n"


def _fetch_snippet() -> str:
    blocks = re.findall(r"```python\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert len(blocks) == 1
    return "\n".join(line.removeprefix("   ") for line in blocks[0].splitlines()) + "\n"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/apply.py")
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"#" * (1024 * 1024 + 1))
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(FAKE_SCRIPT)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _run(tmp_path: Path, url: str, sha: str) -> subprocess.CompletedProcess[str]:
    tmp = tmp_path / "tmp"
    tmp.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-", url, sha, "http://127.0.0.1:1/plan", "3"],
        input=_fetch_snippet(),
        capture_output=True,
        text=True,
        env={"PATH": "", "TMPDIR": str(tmp)},
        timeout=60,
    )


def test_runs_the_fetched_script_when_sha256_matches(tmp_path: Path, base_url: str) -> None:
    sha = hashlib.sha256(FAKE_SCRIPT).hexdigest()
    res = _run(tmp_path, f"{base_url}/apply.py", sha)
    # 終了コードをそのまま返し、引数を渡し（localhost の http なので --allow-http 付き）、残さない
    assert res.returncode == 7, res.stderr
    assert (
        res.stdout.strip()
        == "ran --plan-url http://127.0.0.1:1/plan --expect-generation 3 --allow-http"
    )
    assert list((tmp_path / "tmp").iterdir()) == []


def test_does_not_run_when_sha256_differs(tmp_path: Path, base_url: str) -> None:
    res = _run(tmp_path, f"{base_url}/apply.py", "0" * 64)
    assert res.returncode not in (0, 7)
    assert "ran" not in res.stdout
    assert list((tmp_path / "tmp").iterdir()) == []


def test_refuses_redirects(tmp_path: Path, base_url: str) -> None:
    sha = hashlib.sha256(FAKE_SCRIPT).hexdigest()
    res = _run(tmp_path, f"{base_url}/redirect", sha)
    assert res.returncode not in (0, 7)
    assert "ran" not in res.stdout
    assert "リダイレクト" in res.stderr


def test_refuses_oversized_body(tmp_path: Path, base_url: str) -> None:
    sha = hashlib.sha256(b"#" * (1024 * 1024 + 1)).hexdigest()
    res = _run(tmp_path, f"{base_url}/big", sha)
    assert res.returncode not in (0, 7)
    assert "実行しない" in res.stderr


def test_refuses_plain_http_to_other_hosts(tmp_path: Path) -> None:
    res = _run(tmp_path, "http://example.com/apply.py", "0" * 64)
    assert res.returncode not in (0, 7)
    assert "https" in res.stderr
