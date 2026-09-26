"""replay（AC-013 / NFR-002）: 凍結した IR・ドメインから判定を再評価し、保存された結果と比較する。

LLM を再実行する再抽出とは区別する。再評価は決定的なので、同じ入力から同じ判定が出るはず。
規約 Finding はスナップショット（対象リポジトリのファイル）が必要だが、run 成果物には
凍結した IR しか残さないため再評価はせず、保存済みの値をそのまま比較対象から除く（S11）。

S42: manifest に `policy_revision` が記録されているとき、`resolve_revision` が渡されていれば
その改訂を解決できることを確認する（解決できなければ失敗させる）。これは「規約が変わっても
過去の判定の再現性を保証する」という設計（design/2026-09-26-team-policies-and-dx.md）の
最小限の保証で、規約 Finding 自体の再評価（実際の検証器の再実行）はスナップショットが無いため
まだ行わない。代わりに、保存された規約 Finding がその改訂のルール集合と整合しているか
（改訂に無いルールの Finding が無いか、level と severity が食い違っていないか）を確かめる。
`resolve_revision` を渡さなければ従来どおり規約を考慮せず再評価する。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from speclino.conformance.engine import EngineConfig, evaluate
from speclino.domain.code_ir import CodeIR
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import Findings
from speclino.domain.policy import PolicyRevision
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


def replay(
    run_dir: Path,
    resolve_revision: Callable[[str, int], PolicyRevision | None] | None = None,
) -> ReplayResult:
    manifest = RunManifest.model_validate(json.loads((run_dir / "run-manifest.json").read_text()))
    revision: PolicyRevision | None = None
    if manifest.policy_revision is not None and resolve_revision is not None:
        revision = _resolve_or_fail(manifest, resolve_revision)
    spec = SpecIR.model_validate(json.loads((run_dir / "spec-ir.json").read_text()))
    code_path = run_dir / "code-ir.json"
    if not code_path.exists():
        code_path = run_dir / f"code-ir.{spec.behavior_id}.json"
    code = CodeIR.model_validate(json.loads(code_path.read_text()))
    domain = DomainSpec.model_validate(json.loads((run_dir / "domain.json").read_text()))
    saved = Findings.model_validate(json.loads((run_dir / "findings.json").read_text()))
    if revision is not None:
        _check_policy_findings_against(revision, saved)
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


def _resolve_or_fail(
    manifest: RunManifest, resolve_revision: Callable[[str, int], PolicyRevision | None]
) -> PolicyRevision:
    """記録された改訂が解決できることを確認する。黙って規約なしで再評価すると、過去の
    判定と一致しない理由が分からなくなるため、解決できないときは失敗させる。
    """
    assert manifest.policy_revision is not None
    if manifest.policy_team is None:
        raise ValueError(
            f"run-manifest に policy_revision={manifest.policy_revision} があるが "
            "policy_team が無い"
        )
    revision = resolve_revision(manifest.policy_team, manifest.policy_revision)
    if revision is None:
        raise ValueError(
            "適用したチーム規約の改訂を解決できない: "
            f"team={manifest.policy_team} revision={manifest.policy_revision}"
        )
    return revision


def _check_policy_findings_against(revision: PolicyRevision, saved: Findings) -> None:
    """保存された規約 Finding が、解決した改訂のルール集合に収まっているか確かめる。

    検証器の再実行はできない（成果物に対象リポジトリのスナップショットを残していないため）。
    それでも「manifest が指す改訂に無いルールの Finding が保存されている」ことは検出できる。
    これは manifest が別の改訂を指している、または改訂の内容が入れ替わったことを意味するので、
    黙って通すと「規約を変えても過去の判定は変わらない」という保証が空手形になる。

    プロジェクト固有の追加規約（`.speclino/policy/`）由来の Finding は改訂に無くて当然なので、
    照合の対象にしない。区別できるのはチーム改訂に同じ id があるかどうかだけなので、
    ここでは「改訂に載っている id の Finding」だけを見る向きで検査する。
    """
    rule_levels = {rule.id: rule.level for rule in revision.package.rules}
    mismatched: list[str] = []
    for f in saved.findings:
        if not f.behavior_id.startswith("policy:"):
            continue
        # 規約 Finding の requirement_id は `<rule>:<path>` の 2 段。完全一致で引くと
        # 実際の Finding が 1 件も一致せず検査が空回りする（codex レビュー指摘）。
        # ルール ID の取り出し方は conformance/exceptions.py の `_rule_of` と同じ規約。
        level = rule_levels.get(f.requirement_id.split(":", 1)[0])
        if level is None:
            continue  # プロジェクト固有の追加規約。改訂に無くて当然
        expected = "high" if level == "mandatory" else "medium"
        if f.verdict != "VERIFIED_IN_SCOPE" and f.severity != expected:
            mismatched.append(f"{f.requirement_id}（level={level} なら severity={expected}）")
    if mismatched:
        raise ValueError(
            "保存された規約 Finding が改訂 "
            f"{revision.team}@{revision.revision} と整合しない: {', '.join(sorted(mismatched))}"
        )
