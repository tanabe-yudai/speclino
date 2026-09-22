"""ローカル Docker のサンドボックス（S31）。ネットワーク遮断・読取り専用・非 root・資源制限。

- テストと pytest 設定は base 由来（信頼側）、実装は head 由来（未信頼）を別ディレクトリに置く
- 実行できるのは宣言済みテスト ID だけ。任意のコマンドは受けない
- 停止はコンテナ外（`docker run` のタイムアウト → `docker kill`）から行う
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from speclino.ports.sandbox import SandboxResult

DEFAULT_IMAGE = "speclino-sandbox-runner:latest"
PYTEST_INI = "[pytest]\naddopts = -p no:cacheprovider\n"


class DockerSandbox:
    def __init__(self, image: str = DEFAULT_IMAGE, docker: str = "docker") -> None:
        self.image = image
        self.docker = docker

    def run_tests(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str], timeout_s: int = 120
    ) -> SandboxResult:
        if not test_ids:
            return SandboxResult(2, None, "テスト ID が無い", False, "docker_local")
        work = Path(tempfile.mkdtemp(prefix="speclino-sandbox-"))
        out = work / "out"
        out.mkdir()
        try:
            shutil.copytree(head_root, work / "head", symlinks=False)
            mapping = _copy_declared_tests(base_tests_root, work / "tests", test_ids)
            if not mapping:
                return SandboxResult(2, None, "宣言済みテストが base に無い", False, "docker_local")
            (work / "tests" / "pytest.ini").write_text(PYTEST_INI)  # 設定は SpecLino 側で固定
            name = f"speclino-{uuid.uuid4().hex[:12]}"
            cmd = [
                self.docker,
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,size=64m",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-u",
                "65534",
                "-v",
                f"{work / 'head'}:/work/head:ro",
                "-v",
                f"{work / 'tests'}:/work/tests:ro",
                "-v",
                f"{out}:/work/out:rw",
                "-e",
                "PYTHONPATH=/work/head",
                "-e",
                "PYTHONSAFEPATH=1",
                "-w",
                "/work/tests",
                self.image,
                "-c",
                "/work/tests/pytest.ini",
                "--rootdir",
                "/work/tests",
                "--import-mode=importlib",
                "--junitxml",
                "/work/out/junit.xml",
                "-q",
                *[f"/work/tests/{t}" for t in mapping.values()],
            ]
            timed_out = False
            try:
                proc = subprocess.run(  # noqa: S603 - 引数は固定。宣言済みテスト ID だけを渡す
                    cmd, capture_output=True, timeout=timeout_s, check=False
                )
                stdout, code = proc.stdout + proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as e:
                timed_out = True
                subprocess.run([self.docker, "kill", name], capture_output=True, check=False)
                stdout, code = (e.stdout or b"") + (e.stderr or b""), -1
            junit_path = out / "junit.xml"
            junit = junit_path.read_text() if junit_path.exists() else None
            if junit is not None:
                # 平坦化したファイル名を元の ID に戻す（file 属性と classname の両方）
                for orig, flat in mapping.items():
                    flat_file = flat.split("::", 1)[0]
                    orig_file = orig.split("::", 1)[0]
                    orig_mod = orig_file.removesuffix(".py").replace("/", ".")
                    junit = junit.replace(f'file="{flat_file}"', f'file="{orig_file}"')
                    junit = junit.replace(
                        f'classname="{flat_file.removesuffix(".py")}"', f'classname="{orig_mod}"'
                    )
            return SandboxResult(
                code, junit, stdout.decode(errors="replace")[-2000:], timed_out, "docker_local"
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _copy_declared_tests(base_root: Path, dest: Path, test_ids: list[str]) -> dict[str, str]:
    """base から宣言済みテストを **平坦な名前** で写し、元 ID → サンドボックス内 ID の対応を返す。

    テストをパッケージ階層のまま置くと、pytest がその階層から `examples` 等の名前空間パッケージを
    先に登録し、head ではなく base（テスト側）の実装を import してしまう。平坦化してモジュール名の
    衝突を無くす。conftest.py は最も近い祖先のものだけを写す（複数あれば最初のもの）。
    """
    dest.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    conftest_done = False
    for tid in test_ids:
        path_part, _, name = tid.partition("::")
        rel = Path(path_part)
        if rel.is_absolute() or ".." in rel.parts or not (base_root / rel).is_file():
            continue
        flat = "t_" + hashlib.sha256(path_part.encode()).hexdigest()[:10] + "_" + rel.name
        if not (dest / flat).exists():
            shutil.copy(base_root / rel, dest / flat)
        mapping[tid] = f"{flat}::{name}" if name else flat
        if not conftest_done:
            for parent in [rel.parent, *rel.parent.parents]:
                cf = base_root / parent / "conftest.py"
                if cf.is_file():
                    shutil.copy(cf, dest / "conftest.py")
                    conftest_done = True
                    break
    return mapping
