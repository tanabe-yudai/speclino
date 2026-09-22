"""サンドボックスが無い環境（本番で PoC 不成立のとき等）。常に「実行できない」を返す。"""

from __future__ import annotations

from pathlib import Path

from speclino.ports.sandbox import SandboxResult


class DisabledSandbox:
    def run_tests(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str], timeout_s: int = 120
    ) -> SandboxResult:
        return SandboxResult(2, None, "この環境ではテスト実行が無効", False, "disabled")
