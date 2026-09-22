"""Sandbox ポート: 隔離環境で宣言済みテストを実行し、junit を返す。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    junit_xml: str | None
    stdout_tail: str
    timed_out: bool
    runner: str  # 実行方式の識別（docker_local / cloud_run_sandbox / disabled）


class Sandbox(Protocol):
    def run_tests(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str], timeout_s: int
    ) -> SandboxResult: ...


__all__ = ["Sandbox", "SandboxResult"]
