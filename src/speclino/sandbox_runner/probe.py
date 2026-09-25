"""sandbox ロールの疎通確認（S38）。Cloud Run sandboxes が本当に使えるかだけを確かめる。

本実装（tar を受け取って宣言済みテストを走らせる）に入る前に、最大の未知である
「このプロジェクト・このリージョンで Preview が動くか」を最小の手数で潰すための診断口。
固定コマンドしか実行せず、要求本文を受け取らない。呼べるのは worker SA だけ
（Cloud Run の IAM。infra/envs/prod/run.tf の sandbox_invoker）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/internal/sandbox", tags=["internal"])

# Cloud Run sandboxes が sandbox_launcher 付きコンテナに配置する CLI
# （cloud.google.com/run/docs/configuring/services/sandboxes）。
SANDBOX_CLI = Path("/usr/local/gcp/bin/sandbox")
PROBE_TIMEOUT_S = 60


@router.get("/selftest")
def selftest() -> dict[str, Any]:
    """`sandbox do -- /bin/echo ok` を 1 回だけ実行し、観測できた事実をそのまま返す。

    成否を判定せず、診断に必要な素材（CLI の有無・終了コード・出力）を返すに留める。
    """
    facts: dict[str, Any] = {
        "cli_path": str(SANDBOX_CLI),
        "cli_exists": SANDBOX_CLI.is_file(),
        "cli_executable": os.access(SANDBOX_CLI, os.X_OK) if SANDBOX_CLI.is_file() else False,
        "python": shutil.which("python3") or shutil.which("python"),
        "revision": os.environ.get("K_REVISION"),
        "service": os.environ.get("K_SERVICE"),
    }
    if not facts["cli_exists"]:
        facts["result"] = "cli_not_found"
        return facts

    cmd = [str(SANDBOX_CLI), "do", "--", "/bin/echo", "ok"]
    try:
        proc = subprocess.run(  # noqa: S603 - 固定コマンド。外部入力を受け取らない
            cmd, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        facts["result"] = "timeout"
        return facts
    except OSError as e:  # 実行権限が無い等
        facts["result"] = "exec_error"
        facts["error"] = str(e)
        return facts

    facts["result"] = "ran"
    facts["exit_code"] = proc.returncode
    facts["stdout"] = proc.stdout.decode(errors="replace")[-2000:]
    facts["stderr"] = proc.stderr.decode(errors="replace")[-2000:]
    return facts


# 本実装が成り立つ条件は 3 つ: tar でファイルを持ち込める / 中で python が動く /
# 変更したファイルを tar で回収できる。これも固定の手順で 1 度に確かめる。
TAR_PROBE_SCRIPT = (
    "import pathlib;"
    "p=pathlib.Path('/work/in.txt');"
    "pathlib.Path('/work/out.txt').write_text(p.read_text().upper())"
)


@router.get("/selftest/tar")
def selftest_tar() -> dict[str, Any]:
    """tar の往復と、サンドボックス内の python の有無を確かめる。固定の内容しか扱わない。"""
    if not SANDBOX_CLI.is_file():
        return {"result": "cli_not_found"}
    python = shutil.which("python3") or shutil.which("python")
    if python is None:
        return {"result": "no_python_in_parent"}

    work = Path(tempfile.mkdtemp(prefix="speclino-tarprobe-"))
    facts: dict[str, Any] = {"python": python}
    try:
        payload = work / "work"
        payload.mkdir()
        (payload / "in.txt").write_text("hello sandbox\n")
        in_tar, out_tar = work / "in.tar", work / "out.tar"
        with tarfile.open(in_tar, "w") as tar:
            tar.add(payload, arcname="work")

        cmd = [
            str(SANDBOX_CLI),
            "do",
            "--write",
            f"--import-tar={in_tar}",
            f"--export-tar={out_tar}",
            "--",
            python,
            "-c",
            TAR_PROBE_SCRIPT,
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - 固定コマンド。外部入力を受け取らない
                cmd, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired:
            facts["result"] = "timeout"
            return facts
        facts["exit_code"] = proc.returncode
        facts["stdout"] = proc.stdout.decode(errors="replace")[-2000:]
        facts["stderr"] = proc.stderr.decode(errors="replace")[-2000:]
        facts["export_tar_exists"] = out_tar.is_file()
        if out_tar.is_file():
            with tarfile.open(out_tar) as tar:
                names = tar.getnames()
            facts["export_members"] = names[:50]
            facts["found_out_txt"] = any(n.endswith("out.txt") for n in names)
        facts["result"] = "ran"
        return facts
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --mount の書式は help に載っていないので、ありそうな書き方を一度に試して当たりを探す。
# デプロイが 1 回数分かかるため、仮説を小分けにせずまとめて確かめる。
MOUNT_SYNTAXES: tuple[tuple[str, str], ...] = (
    ("colon", "{src}:/work"),
    ("colon_rw", "{src}:/work:rw"),
    ("src_dst", "src={src},dst=/work"),
    ("bind", "type=bind,src={src},dst=/work"),
    ("same_path", "{src}"),
)


@router.get("/selftest/mount")
def selftest_mount() -> dict[str, Any]:
    """`--mount` の書式候補を順に試し、サンドボックス内からファイルが見えたものを報告する。"""
    if not SANDBOX_CLI.is_file():
        return {"result": "cli_not_found"}
    python = shutil.which("python3") or shutil.which("python")
    if python is None:
        return {"result": "no_python_in_parent"}

    work = Path(tempfile.mkdtemp(prefix="speclino-mountprobe-"))
    attempts: list[dict[str, Any]] = []
    try:
        (work / "in.txt").write_text("hello sandbox\n")
        for name, template in MOUNT_SYNTAXES:
            mount = template.format(src=work)
            # マウント先が候補ごとに変わるので、両方の置き場所を見に行く。
            script = (
                "import pathlib;"
                f"cands=[pathlib.Path('/work/in.txt'),pathlib.Path({str(work)!r})/'in.txt'];"
                "hit=[str(c) for c in cands if c.is_file()];"
                "print('FOUND',hit)"
            )
            cmd = [
                str(SANDBOX_CLI),
                "do",
                "--write",
                "--mount",
                mount,
                "--",
                python,
                "-c",
                script,
            ]
            record: dict[str, Any] = {"name": name, "mount": mount}
            try:
                proc = subprocess.run(  # noqa: S603 - 候補は固定。外部入力を受け取らない
                    cmd, capture_output=True, timeout=30, check=False
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                record["error"] = str(e)
                attempts.append(record)
                continue
            out = (proc.stdout + proc.stderr).decode(errors="replace")
            record["exit_code"] = proc.returncode
            record["output"] = out[-500:]
            record["visible"] = "FOUND ['" in out
            attempts.append(record)
        return {"result": "ran", "host_dir": str(work), "attempts": attempts}
    finally:
        shutil.rmtree(work, ignore_errors=True)


MOUNT = "type=bind,src={src},dst={dst}"  # --mount の書式（selftest/mount で特定）

PROBE_MODULE = "def add(a, b):\n    return a - b  # わざと間違える\n"
PROBE_TEST = (
    "import socket\n"
    "from mod import add\n"
    "\n"
    "def test_add_is_wrong():\n"
    "    assert add(1, 2) == 3\n"
    "\n"
    "def test_network_is_blocked():\n"
    "    socket.setdefaulttimeout(5)\n"
    "    socket.create_connection(('example.com', 80))\n"
)


@router.get("/selftest/pytest")
def selftest_pytest() -> dict[str, Any]:
    """本実装と同じ形で 1 回走らせ、設計が成立するかを確かめる。

    見たいのは 3 点。junit がホスト側の bind mount に出るか／pytest が動くか／
    ネットワークが遮断されているか（遮断されていればネットワークのテストが失敗する）。
    """
    if not SANDBOX_CLI.is_file():
        return {"result": "cli_not_found"}
    python = shutil.which("python3") or shutil.which("python")
    if python is None:
        return {"result": "no_python_in_parent"}

    work = Path(tempfile.mkdtemp(prefix="speclino-pytestprobe-"))
    try:
        head, tests, out = work / "head", work / "tests", work / "out"
        for d in (head, tests, out):
            d.mkdir()
        (head / "mod.py").write_text(PROBE_MODULE)
        (tests / "test_probe.py").write_text(PROBE_TEST)

        cmd = [
            str(SANDBOX_CLI),
            "do",
            "--write",
            "--mount",
            MOUNT.format(src=head, dst="/work/head"),
            "--mount",
            MOUNT.format(src=tests, dst="/work/tests"),
            "--mount",
            MOUNT.format(src=out, dst="/work/out"),
            "--workdir",
            "/work/tests",
            "-e",
            "PYTHONPATH=/work/head",
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
            "/work/tests/test_probe.py",
        ]
        facts: dict[str, Any] = {}
        try:
            proc = subprocess.run(  # noqa: S603 - 固定の題材。外部入力を受け取らない
                cmd, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired:
            return {"result": "timeout"}
        facts["result"] = "ran"
        facts["exit_code"] = proc.returncode
        facts["stdout"] = (proc.stdout + proc.stderr).decode(errors="replace")[-3000:]
        junit = out / "junit.xml"
        facts["junit_on_host"] = junit.is_file()
        if junit.is_file():
            facts["junit"] = junit.read_text()[:3000]
        return facts
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.get("/selftest/help")
def selftest_help() -> dict[str, Any]:
    """CLI 自身の使い方を取り出す。フラグの意味を推測で試さないための一次情報。"""
    if not SANDBOX_CLI.is_file():
        return {"result": "cli_not_found"}
    out: dict[str, Any] = {"result": "ran"}
    for key, args in (("root", ["--help"]), ("do", ["do", "--help"]), ("run", ["run", "--help"])):
        try:
            proc = subprocess.run(  # noqa: S603 - 固定コマンド。外部入力を受け取らない
                [str(SANDBOX_CLI), *args], capture_output=True, timeout=30, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            out[key] = f"error: {e}"
            continue
        out[key] = (proc.stdout + proc.stderr).decode(errors="replace")[:6000]
    return out


__all__ = ["router"]
