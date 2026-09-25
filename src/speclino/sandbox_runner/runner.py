"""sandbox ロールの本実装（S38）: tar で受け取った head と宣言済みテストを隔離実行し junit を返す。

`probe.py` の `selftest_pytest()` で成功が確認できた形（`sandbox do --write --mount
type=bind,src=<host>,dst=<sandbox> ...` を 3 本、`python -m pytest --junitxml=...`）をそのまま使う。
違うのは、head とテストを worker がその場でマウントできる場所に置くのではなく、HTTP で tar
として持ち込む点だけ（sandbox サービスは権限ゼロの SA で GCS を読めないため）。

呼べるのは worker SA だけ（Cloud Run の IAM。`/internal/tasks/run` と同じ考え方で、OIDC の検証は
Cloud Run 自身に委ねる）。それでも、渡された tar とテスト ID、そしてサンドボックス内で実行される
未信頼コードそのものは信頼しない。このハンドラは「サンドボックス内の未信頼コードが親プロセス
（この HTTP サーバ自身）を攻撃する」経路を塞ぐことを狙う:

- tar の展開は `tarfile` の `filter="data"`（PEP 706）で行い、`..` や絶対パスを含むメンバー、
  デバイスファイル等を拒否する。展開前にはメンバー数・個別サイズ・合計サイズの上限も検査し、
  高圧縮 tar によるディスク枯渇（zip bomb 相当）を拒否する。
- 実行するテスト ID は、展開した tests/ に実在するものだけに絞る（`docker_local.py` の
  `copy_declared_tests` と同じ考え方: 絶対パス・`..` を拒否し、実在確認をしてから使う）。
  宣言と無関係な文字列を pytest の引数に紛れ込ませない。
- pytest の標準出力は上限を設けて読み取る。`subprocess.run(capture_output=True)` は全量を
  親のメモリに溜めるため、大量出力でタイムアウトより先に親プロセスを OOM で落とせてしまう。
- junit.xml は `os.O_NOFOLLOW` で開いた fd を `os.fstat` で検査してから読む。実行中に
  `/work/out/junit.xml` がホスト側の任意のファイルへのシンボリックリンクに差し替えられても、
  それを追ってこのプロセス自身の秘密（環境変数等）を漏らさないようにする。
- タイムアウト時の `sandbox delete --force` は終了コードを確認し、失敗を握りつぶさない。
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path
from typing import IO

from fastapi import APIRouter
from pydantic import BaseModel, Field

from speclino.api.errors import api_error
from speclino.sandbox_runner.probe import MOUNT, SANDBOX_CLI

router = APIRouter(prefix="/internal/sandbox", tags=["internal"])

# --- 展開する tar の容量上限（codex レビュー指摘: zip bomb でホストのディスクを枯渇できる）---
# 実際の head + 宣言済みテスト一式はこれよりずっと小さい。tarfile はメンバーのヘッダに書かれた
# サイズだけ読み出して書き込むため、展開前にヘッダの宣言値を検査すれば実際の書き込み量を
# 上から抑えられる（gzip 側がどれだけ高圧縮でも関係ない）。
TAR_MAX_MEMBERS = 20_000
TAR_MAX_MEMBER_BYTES = 32 * 1024 * 1024  # 32 MiB: 単一ファイルの妥当な上限
TAR_MAX_TOTAL_BYTES = 256 * 1024 * 1024  # 256 MiB: 展開後の合計サイズの上限

# --- pytest の標準出力の保持上限（codex レビュー指摘: 全量を溜めると OOM で親を落とせる）---
# 失敗原因の特定に使うのは末尾だけで十分なので、それ以外は読み捨てる。
STDOUT_CAP_BYTES = 1 * 1024 * 1024  # 1 MiB

# --- junit.xml として読んでよい上限（codex レビュー指摘: シンボリックリンク越しの読み取り対策）---
JUNIT_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB: 通常の junit.xml はこれよりずっと小さい

# sandbox delete --force 自体がハングして応答が返らなくなるのを防ぐ上限
SANDBOX_DELETE_TIMEOUT_S = 30


class SandboxRunRequest(BaseModel):
    tar_b64: str  # head/ と tests/（宣言済みテスト＋pytest.ini）をまとめた tar
    test_ids: list[str]  # tests/ 配下でのテスト ID（クライアント側で平坦化済み）
    timeout_s: int = Field(default=120, gt=0)


class SandboxRunResponse(BaseModel):
    exit_code: int
    junit_xml: str | None
    stdout_tail: str
    timed_out: bool
    runner: str = "cloud_run_sandbox"


def _check_tar_limits(members: list[tarfile.TarInfo]) -> None:
    """展開前にメンバー数・個別サイズ・合計サイズを検査し、超えていれば拒否する。

    `filter="data"` は traversal（`..` や絶対パス、デバイスファイル等）を防ぐが容量には
    無頓着。展開は subprocess のタイムアウトが始まる前に走るため、実行時間の制限も効かない。
    """
    if len(members) > TAR_MAX_MEMBERS:
        raise api_error(
            400, "tar_too_many_members", f"tar のメンバー数が多すぎる（{len(members)} 件）"
        )
    total = 0
    for m in members:
        if m.size > TAR_MAX_MEMBER_BYTES:
            raise api_error(
                400,
                "tar_member_too_large",
                f"tar のメンバーが大きすぎる: {m.name}（{m.size} bytes）",
            )
        total += m.size
        if total > TAR_MAX_TOTAL_BYTES:
            raise api_error(
                400, "tar_too_large", f"tar の展開後合計サイズが大きすぎる（{total} bytes）"
            )


def _extract_tar(tar_b64: str, dest: Path) -> None:
    """base64 の tar を安全に展開する。"""
    try:
        raw = base64.b64decode(tar_b64, validate=True)
    except binascii.Error as e:
        raise api_error(400, "invalid_tar_b64", f"base64 として不正: {e}") from e
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            # filter="data": ".." や絶対パス、シンボリックリンクでの脱出、デバイスファイル等を
            # 拒否する（PEP 706）。ここが path traversal を防ぐ唯一の関門なので固定で使う
            # （容量の検査はこれとは別の話で、traversal 防御を弱めるものではない）。
            _check_tar_limits(tar.getmembers())
            tar.extractall(dest, filter="data")
    except tarfile.TarError as e:
        raise api_error(400, "invalid_tar", f"tar の展開に失敗: {e}") from e


def _declared_test_ids(tests_dir: Path, requested: list[str]) -> list[str]:
    """要求されたテスト ID のうち、展開した tests/ に実在するものだけを残す。

    `copy_declared_tests` と同じ考え方: パスが絶対パス・`..` を含まず、かつ実際に
    tests_dir 配下のファイルを指しているものだけを「宣言済み」として扱う。
    """
    declared: list[str] = []
    for tid in requested:
        path_part, _, _name = tid.partition("::")
        rel = Path(path_part)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        if (tests_dir / rel).is_file():
            declared.append(tid)
    return declared


def _run_capped(cmd: list[str], timeout_s: float) -> tuple[bytes, int]:
    """cmd を実行し、結合した標準出力の末尾 `STDOUT_CAP_BYTES` だけを保持して返す。

    `subprocess.run(capture_output=True)` は出力を全部親プロセスのメモリに溜める。サンドボックス
    内の未信頼コードが大量に出力すると、タイムアウトより先に親サービスを OOM で落とせる。ここでは
    `Popen` + 別スレッドの読み取りで、末尾だけをメモリに残し残りは読み捨てる。読むのをやめると
    OS のパイプが詰まって子プロセスの write がブロックし、サンドボックスのタイムアウトより先に
    ハングしうるため、上限超過後も読み続ける。

    タイムアウトした場合は `subprocess.run` と同じ契約に揃えるため `subprocess.TimeoutExpired`
    を送出する（`.stdout` にその時点までの末尾を積む）。呼び出し側の「タイムアウト時に
    `sandbox delete --force` する」処理は変えずに済む。
    """
    proc = subprocess.Popen(  # noqa: S603 - 引数は呼び出し元が組み立てた固定コマンド
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    assert proc.stdout is not None  # stdout=PIPE を指定したので必ず開いている
    stream: IO[bytes] = proc.stdout
    tail = bytearray()

    def _drain() -> None:
        for chunk in iter(lambda: stream.read(65536), b""):
            tail.extend(chunk)
            # 上限の倍まで溜まったら切り詰める（毎チャンクで切り詰めると O(n^2) になる）
            if len(tail) > STDOUT_CAP_BYTES * 2:
                del tail[: len(tail) - STDOUT_CAP_BYTES]

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()  # kill 後の後始末（zombie 化を防ぐ。子の stdout も閉じ reader は EOF で終わる）
        reader.join()
        raise subprocess.TimeoutExpired(
            cmd, timeout_s, output=bytes(tail[-STDOUT_CAP_BYTES:])
        ) from exc
    reader.join()
    return bytes(tail[-STDOUT_CAP_BYTES:]), code


def _delete_sandbox(name: str) -> str | None:
    """タイムアウトした sandbox を `delete --force` で片付ける。失敗したら理由を返す。

    成功したように見せない: 終了コードを確認せずに無視すると、削除に失敗して sandbox が
    残っていても通常のタイムアウト応答をそのまま返してしまう。delete 自体にも上限を付け、
    ハングして応答が返らなくなることも避ける。
    """
    try:
        proc = subprocess.run(  # noqa: S603 - 固定コマンド、name はこのプロセスが払い出した値
            [str(SANDBOX_CLI), "delete", "--force", name],
            capture_output=True,
            timeout=SANDBOX_DELETE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"sandbox delete --force {name} がタイムアウトした（sandbox が残っている可能性）"
    except OSError as e:
        return f"sandbox delete --force {name} の実行に失敗: {e}"
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).decode(errors="replace")[-500:]
        return f"sandbox delete --force {name} が失敗した（exit={proc.returncode}）: {detail}"
    return None


def _read_junit_safely(path: Path) -> tuple[str | None, str | None]:
    """junit.xml をシンボリックリンク越しに読まない。

    tar 展開時の `filter="data"` は展開時点のメンバーしか検査せず、pytest 実行中に未信頼
    コードが `/work/out/junit.xml` を `/proc/self/environ` 等ホスト側の任意のファイルへの
    シンボリックリンクに差し替えても防げない。`os.O_NOFOLLOW` で開き、得られた fd に対して
    `os.fstat` で通常ファイル（`stat.S_ISREG`）であることとサイズ上限を確認してから読む
    （fd は open した瞬間の実体を指し続けるので、open 後に path 側を差し替える TOCTOU にも
    強い）。

    戻り値は (内容 or None, 読めなかった理由 or None)。ファイルが単に無い場合（pytest が
    起動すら出来なかった等）は異常ではないので理由を None にする。
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        # シンボリックリンクだった場合は O_NOFOLLOW により ELOOP になる
        return None, f"junit.xml を安全に開けなかった（シンボリックリンクの可能性）: {e}"

    should_close = True
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "junit.xml が通常ファイルでない"
        if st.st_size > JUNIT_MAX_BYTES:
            return None, f"junit.xml が大きすぎる（{st.st_size} bytes）"
        with os.fdopen(fd, "rb") as f:
            should_close = False
            return f.read().decode(errors="replace"), None
    finally:
        if should_close:
            os.close(fd)


@router.post("/run")
def run(body: SandboxRunRequest) -> SandboxRunResponse:
    if not SANDBOX_CLI.is_file():
        raise api_error(
            503, "sandbox_cli_not_found", "sandbox CLI が無い（sandbox_launcher でない環境）"
        )
    python = shutil.which("python3") or shutil.which("python")
    if python is None:
        raise api_error(503, "python_not_found", "親コンテナに python が無い")

    work = Path(tempfile.mkdtemp(prefix="speclino-sandbox-run-"))
    try:
        # tar は "in" だけに展開する。"out" はこのプロセスが作る専用の出力先で、
        # tar のメンバー（信頼しない）では絶対に触れない場所にしておく（junit.xml の差し替え防止）。
        in_dir, out = work / "in", work / "out"
        _extract_tar(body.tar_b64, in_dir)
        out.mkdir(parents=True, exist_ok=True)
        head, tests = in_dir / "head", in_dir / "tests"
        if not head.is_dir() or not tests.is_dir():
            raise api_error(400, "invalid_layout", "tar に head/ または tests/ が無い")

        test_ids = _declared_test_ids(tests, body.test_ids)
        if not test_ids:
            return SandboxRunResponse(
                exit_code=2,
                junit_xml=None,
                stdout_tail="宣言済みテストが tar に無い",
                timed_out=False,
            )

        # 削除に使う決定的な名前（タイムアウト時に `sandbox delete --force <name>` で確実に止める）
        name = f"speclino-sbx-{uuid.uuid4().hex[:16]}"
        cmd = [
            str(SANDBOX_CLI),
            "do",
            "--write",
            "--sandbox-name",
            name,
            "--mount",
            MOUNT.format(src=head, dst="/work/head"),
            "--mount",
            MOUNT.format(src=tests, dst="/work/tests"),
            "--mount",
            MOUNT.format(src=out, dst="/work/out"),
            "--workdir",
            "/work/tests",
            "-e",
            # head/ 直下（examples/... のような相対 import 用）と head/src（src レイアウトの
            # パッケージ用）の両方を通す。site-packages より先に来るので、この容器に
            # インストール済みの実装ではなく head の実装が import される。
            "PYTHONPATH=/work/head:/work/head/src",
            "-e",
            "PYTHONSAFEPATH=1",
            "--",
            python,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--junitxml=/work/out/junit.xml",
            "-q",
            *[f"/work/tests/{t}" for t in test_ids],
        ]
        timed_out = False
        delete_error: str | None = None
        try:
            stdout, code = _run_capped(cmd, body.timeout_s)
        except subprocess.TimeoutExpired as e:
            timed_out = True
            delete_error = _delete_sandbox(name)
            stdout, code = bytes(e.stdout or b""), -1

        junit, junit_note = _read_junit_safely(out / "junit.xml")
        notes = []
        if delete_error:
            notes.append(f"[timeout] {delete_error}")
        if junit_note:
            notes.append(f"[junit] {junit_note}")
        stdout_text = stdout.decode(errors="replace")
        if notes:
            stdout_text = stdout_text + "\n" + "\n".join(notes)

        return SandboxRunResponse(
            exit_code=code,
            junit_xml=junit,
            stdout_tail=stdout_text[-2000:],
            timed_out=timed_out,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


__all__ = ["SandboxRunRequest", "SandboxRunResponse", "router"]
