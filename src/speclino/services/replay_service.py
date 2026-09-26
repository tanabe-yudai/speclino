"""replay（AC-013 / NFR-002）: 凍結した IR・ドメインから判定を再評価し、保存された結果と比較する。

LLM を再実行する再抽出とは区別する。再評価は決定的なので、同じ入力から同じ判定が出るはず。
規約 Finding はスナップショットが必要なので再評価せず、保存済みの値をそのまま比較対象から除く。

S42: manifest に `policy_revision` が記録されているとき（チーム規約を適用した run）、
`resolve_revision` が渡されていればその改訂を解決し、`check_pipeline.policy_package_hash` と
同じ手順で再計算したハッシュが manifest の `team_policy_hash` と一致するか確かめる。ルール集合が
入れ替わった改訂（極端には空の改訂）に差し替えても、ハッシュが変われば必ず検出できる
（「ルール ID が改訂に無いから対象外」という抜け道を作らない）。`policy_revision` があるのに
`resolve_revision` が渡されていないときは、黙って規約なしで再評価すると過去の判定と一致しない
理由が分からなくなるため、既定では失敗にする。CLI は DB 接続を持たないため
`require_policy_check=False`（`--no-policy-check`）で明示的に飛ばす経路を用意する。
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
from speclino.services.check_pipeline import policy_package_hash


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
    require_policy_check: bool = True,
) -> ReplayResult:
    manifest = RunManifest.model_validate(json.loads((run_dir / "run-manifest.json").read_text()))
    revision = _verify_team_policy(manifest, resolve_revision, require_policy_check)
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


def _verify_team_policy(
    manifest: RunManifest,
    resolve_revision: Callable[[str, int], PolicyRevision | None] | None,
    require_policy_check: bool,
) -> PolicyRevision | None:
    """manifest が適用したチーム規約の改訂を検査する（S42）。

    `policy_hash` の照合を主にする: 解決した改訂から `policy_package_hash` で同じ手順の
    ハッシュを再計算し、manifest の `team_policy_hash` と一致するか見る。ルール集合が
    入れ替わっていても（改訂が空でも）ハッシュは変わるので検出できる（「改訂に無い ID の
    Finding は対象外」という抜け道を作らない）。

    `policy_revision` が記録されているのに `resolve_revision` が渡されていない場合、黙って
    規約なしで再評価すると過去の判定と一致しない理由が分からなくなるため、既定
    （`require_policy_check=True`）では失敗させる。`require_policy_check=False` を渡した
    ときだけ明示的に飛ばす（CLI の `--no-policy-check` はここに落ちる）。
    """
    if manifest.policy_revision is None:
        return None
    if manifest.policy_team is None:
        raise ValueError(
            f"run-manifest に policy_revision={manifest.policy_revision} があるが "
            "policy_team が無い（manifest が壊れている）"
        )
    if resolve_revision is None:
        if require_policy_check:
            raise ValueError(
                "この run はチーム規約の改訂"
                f"（team={manifest.policy_team} revision={manifest.policy_revision}）を適用して"
                "いる。replay でチーム規約を検証するには resolve_revision（DB 接続）が要る。"
                "CLI からは --no-policy-check で明示的に飛ばせる（規約なしで再評価する）"
            )
        return None
    revision = resolve_revision(manifest.policy_team, manifest.policy_revision)
    if revision is None:
        raise ValueError(
            "適用したチーム規約の改訂を解決できない: "
            f"team={manifest.policy_team} revision={manifest.policy_revision}"
        )
    expected = manifest.inputs.team_policy_hash
    actual = policy_package_hash(revision.package)
    if expected != actual:
        raise ValueError(
            "チーム規約のハッシュが一致しない（記録時と改訂の内容が異なる）: "
            f"team={manifest.policy_team} revision={manifest.policy_revision} "
            f"manifest={expected} 再計算={actual}"
        )
    return revision


def _check_policy_findings_against(revision: PolicyRevision, saved: Findings) -> None:
    """保存された規約 Finding が、解決した改訂のルール集合と整合するか確かめる（補助）。

    主な保証は `_verify_team_policy` のハッシュ照合（ルール集合が入れ替われば必ず検出する）。
    こちらは「level と severity が食い違う」という別方向のずれを見る補助的な検査で、
    ハッシュが一致していても保存結果の側が壊れている場合に気づける。

    規約 Finding の `requirement_id` は `<rule>:<path>` の 2 段なので、ルール ID は先頭の
    区切りまでを取る（`conformance/exceptions.py` の `_rule_of` と同じ規約）。完全一致で
    引くと実際の Finding が 1 件も一致せず検査が空回りする（codex レビュー指摘）。
    """
    rule_levels = {rule.id: rule.level for rule in revision.package.rules}
    mismatched: list[str] = []
    for f in saved.findings:
        if not f.behavior_id.startswith("policy:"):
            continue
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
