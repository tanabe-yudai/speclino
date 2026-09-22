"""replay（AC-013 / NFR-002）: 凍結した IR・ドメインから判定を再評価し、保存された結果と比較する。

LLM を再実行する再抽出とは区別する。再評価は決定的なので、同じ入力から同じ判定が出るはず。
規約 Finding はスナップショットが必要なので再評価せず、保存済みの値をそのまま比較対象から除く。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from speclino.conformance.engine import EngineConfig, evaluate
from speclino.domain.code_ir import CodeIR
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import Findings
from speclino.domain.run import RunManifest
from speclino.domain.spec_ir import SpecIR


@dataclass
class ReplayResult:
    run_id: str
    behavior_id: str
    identical: bool
    compared: int
    differences: list[str] = field(default_factory=list)
    replayed: Findings | None = None


def replay(run_dir: Path) -> ReplayResult:
    manifest = RunManifest.model_validate(json.loads((run_dir / "run-manifest.json").read_text()))
    spec = SpecIR.model_validate(json.loads((run_dir / "spec-ir.json").read_text()))
    code_path = run_dir / "code-ir.json"
    if not code_path.exists():
        code_path = run_dir / f"code-ir.{spec.behavior_id}.json"
    code = CodeIR.model_validate(json.loads(code_path.read_text()))
    domain = DomainSpec.model_validate(json.loads((run_dir / "domain.json").read_text()))
    saved = Findings.model_validate(json.loads((run_dir / "findings.json").read_text()))
    fresh = evaluate(
        spec,
        code,
        domain,
        EngineConfig(solver_timeout_ms=manifest.limits.solver_timeout_ms),
        manifest.run_id,
    )
    saved_by_id = {f.requirement_id: f for f in saved.findings if f.behavior_id == spec.behavior_id}
    diffs: list[str] = []
    for f in fresh.findings:
        old = saved_by_id.get(f.requirement_id)
        if old is None:
            diffs.append(f"{f.requirement_id}: 保存結果に無い")
            continue
        if (old.verdict, old.category, old.id) != (f.verdict, f.category, f.id):
            diffs.append(
                f"{f.requirement_id}: 保存 {old.verdict}/{old.category}/{old.id}"
                f" → 再評価 {f.verdict}/{f.category}/{f.id}"
            )
        old_cex = old.counterexample.assignments if old.counterexample else None
        new_cex = f.counterexample.assignments if f.counterexample else None
        if old_cex != new_cex:
            diffs.append(f"{f.requirement_id}: 反例が異なる {old_cex} → {new_cex}")
    return ReplayResult(
        manifest.run_id, spec.behavior_id, not diffs, len(fresh.findings), diffs, fresh
    )
