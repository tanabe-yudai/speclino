"""調査の文脈（スナップショット、IR、現在の判定）。ツールはここを読み、決定的に更新する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from speclino.domain.bindings import Bindings, OperationContract
from speclino.domain.code_ir import AnalysisLimits, CodeIR
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import Finding, Findings
from speclino.domain.spec_ir import SpecIR
from speclino.ports.sandbox import Sandbox

UNRESOLVED_CATEGORIES_FOR_HUMANS = frozenset(
    {"stale_approval", "vacuous_applicability", "ambiguous_spec", "scope_changed"}
)


@dataclass
class InvestigationContext:
    repo_root: Path
    domain: DomainSpec
    bindings: Bindings
    contracts: dict[str, OperationContract]
    specs: dict[str, SpecIR]  # behavior_id → SpecIR
    findings: Findings
    code_irs: dict[str, CodeIR]  # behavior_id → 最新の Code IR
    limits: AnalysisLimits
    source_roots: tuple[str, ...] = (".", "src")
    events: list[dict[str, Any]] = field(default_factory=list)
    plans_recorded: int = 0
    questions: list[dict[str, Any]] = field(default_factory=list)  # human_ask の登録（未回答）
    base_root: Path | None = None  # base スナップショット（テストと設定の信頼側）
    sandbox: Sandbox | None = None  # 隔離テスト実行（無ければ test_run は使えない）

    def unresolved(self) -> list[Finding]:
        """エージェントが取り組む Finding。人の判断が要る種別は除く。"""
        return [
            f
            for f in self.findings.findings
            if f.verdict in ("UNKNOWN", "REVIEW")
            and f.category not in UNRESOLVED_CATEGORIES_FOR_HUMANS
        ]

    def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.events.append({"seq": len(self.events) + 1, "type": type_, "payload": payload})
