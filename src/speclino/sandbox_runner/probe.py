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


__all__ = ["router"]
