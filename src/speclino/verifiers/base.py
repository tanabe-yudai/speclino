"""規約検証器の共通インターフェース（S11）。

conformance_engine とは別系統。`applies_to` に一致するスナップショットを読み取るだけで、
何も実行しない（Makefile・CI 定義は
読み取りで検査し、未信頼の Makefile をホスト上で評価しない）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRule


@runtime_checkable
class Snapshot(Protocol):
    """検査対象への読取り専用アクセス。"""

    snapshot_id: str

    def read_text(self, path: str) -> str | None:
        """path の内容をテキストで返す。存在しなければ None。"""
        ...

    def glob(self, pattern: str) -> list[str]:
        """glob パターンに一致する相対パスを安定した順序で返す。"""
        ...


@runtime_checkable
class Verifier(Protocol):
    """1つの検証器 ID に対応する実装。"""

    id: str
    version: str

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        """rule を snapshot に対して評価し、Finding を返す。"""
        ...
