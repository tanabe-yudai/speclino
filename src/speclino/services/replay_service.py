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

S65（design/2026-10-05-team-knowledge-registry.md 8 章）: bundle を固定した run（manifest に
`bundle_generation` がある）は `replay_bundle` で再現する。その run のプロジェクトのワークスペースの
bundle だけを世代で引き、署名と manifest の `bundle_hash` を照合してから、決定的な検査（エンジン・
UML の義務・規約の検査器・lock）を同じ run_id・上限・時刻で再実行し、すべての Finding を比べる
（規約の Finding も再評価する。旧 `replay` は規約を比較から外していた）。LLM 支援の Finding
（`assessment_outcome` あり、S70）は記録で再評価するので、ここでは比べずに `llm_findings` に残す。
旧 `replay` は bundle の run を受け付けない（DB と対象のスナップショットが要るため）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.conformance.engine import EngineConfig, evaluate
from speclino.domain.code_ir import CodeIR
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import PolicyRevision
from speclino.domain.run import RunManifest
from speclino.domain.spec_ir import SpecIR
from speclino.services.check_pipeline import (
    CheckRequest,
    llm_assisted_rules,
    policy_package_hash,
    run_check,
)


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
    manifest = _load_manifest(run_dir)
    if manifest.bundle_generation is not None:
        raise ValueError(
            "この run は知識の bundle を固定している。replay_bundle（DB 接続と対象の"
            "スナップショット）で再現する"
        )
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


# worker が書く、エージェントの調査前の決定的な Findings（review_run_service と同じ名前）
DETERMINISTIC_FINDINGS = "findings.deterministic.json"


def _load_manifest(run_dir: Path) -> RunManifest:
    return RunManifest.model_validate(json.loads((run_dir / "run-manifest.json").read_text()))


@dataclass
class BundleReplayResult:
    run_id: str
    bundle_generation: int
    identical: bool
    compared: int
    differences: list[str] = field(default_factory=list)
    # LLM 支援の Finding（S70）。記録で再評価するため、決定的な比較の対象にしない
    llm_findings: list[str] = field(default_factory=list)
    replayed: Findings | None = None


_LLM_BEHAVIOR_ID = "policy:llm.checklist"


def _is_llm_assisted(f: Finding) -> bool:
    """LLM 支援の規約の Finding（REVIEW の評価と、評価できなかった UNKNOWN）。"""
    return f.assessment_outcome is not None or f.behavior_id == _LLM_BEHAVIOR_ID


def _models_by_rule(keys: list[str]) -> dict[str, str]:
    """manifest の評価のキー（正規 JSON）→ キーのルール部分（ID と出所）ごとのモデル。"""
    from speclino.services.llm_policies import parse_key

    out: dict[str, str] = {}
    for text in keys:
        key = parse_key(text)
        if key is not None:
            out.setdefault(key.rule_id, key.model)
    return out


async def _recorded_llm_findings(
    session: AsyncSession, req: CheckRequest, keys: list[str], workspace: str
) -> tuple[dict[str, list[Finding]], list[str]]:
    """LLM 支援の規約を記録だけで再評価する（LLM は呼ばない。記録の無いキーは UNKNOWN）。

    元の run でキーが 1 つも無いルール（LLM の設定が無かった・全部失敗した）は評価しない
    （検査器が「評価していない」UNKNOWN を出す）。
    """
    from speclino.db.repositories.assessments import AssessmentRepository
    from speclino.services.llm_policies import assess, checklist_params, rule_key
    from speclino.services.llm_units import extract_units

    models = _models_by_rule(keys)
    store = AssessmentRepository(session, workspace)
    out: dict[str, list[Finding]] = {}
    used: list[str] = []
    for target in llm_assisted_rules(req):
        rule = target.rule
        model = models.get(rule_key(rule, target.origin))
        if model is None:
            continue
        # AST の走査は CPU を使うので、イベントループを止めないよう別のスレッドで行う
        units = await asyncio.to_thread(
            extract_units, req.repo_root, checklist_params(rule), rule.applies_to.paths
        )
        found, rule_keys = await assess(
            rule,
            units.units,
            llm=None,
            store=store,
            policy_revision=target.policy_revision,
            model=model,
            unassessable=units.problems,
            origin=target.origin,
            now=req.now,
        )
        out[rule.id] = found
        used.extend(rule_keys)
    return out, used


async def replay_bundle(
    session: AsyncSession,
    run_dir: Path,
    *,
    project_id: str,
    repo_root: Path,
    base_root: Path | None = None,
    trusted_root: Path | None = None,
) -> BundleReplayResult:
    """bundle を固定した run を、記録の bundle で再実行して保存結果と比べる。

    bundle は run のプロジェクトの（今の）ワークスペースからだけ引く。リクエストや manifest の
    ワークスペース名は使わない。プロジェクトが別のワークスペースへ移った・bundle が書き換えられた
    場合は `bundle_hash` か署名が合わず ValueError。`repo_root` は run の head のスナップショット、
    PR の run（base あり）は `base_root`（意味差分）、信頼側を記録した run は `trusted_root`
    （manifest の `trusted_commit` のスナップショット。対応・契約・免除・規約を読む）も要る。
    source_roots・today・bindings_patch は manifest の記録を使う（記録の無い古い run は既定値）。
    比較の相手は、記録があればエージェントの調査前の決定的な Findings（DETERMINISTIC_FINDINGS）。
    """
    from speclino.db.models import Project
    from speclino.services import knowledge_registry as kr
    from speclino.services.knowledge_inputs import bundle_hash

    manifest = _load_manifest(run_dir)
    if manifest.bundle_generation is None:
        raise ValueError("この run は bundle を固定していない（replay を使う）")
    if manifest.inputs.base_commit is not None and base_root is None:
        raise ValueError("PR の run の再現には base のスナップショット（base_root）が要る")
    if manifest.trusted_commit is not None and trusted_root is None:
        raise ValueError(f"この run の信頼側（{manifest.trusted_commit}）のスナップショットが要る")
    try:
        project = await session.get(Project, UUID(project_id))
    except ValueError:
        project = None
    if project is None or project.team is None:
        raise LookupError(f"プロジェクト {project_id} はワークスペースに登録されていない")
    bundle = await kr.get_bundle(session, project.team, manifest.bundle_generation)
    if not kr.verify_bundle(bundle):
        raise ValueError(f"bundle {bundle.generation} の署名が合わない")
    if bundle_hash(bundle) != manifest.bundle_hash:
        raise ValueError(
            f"bundle {bundle.generation} のハッシュが記録と合わない"
            f"（manifest={manifest.bundle_hash} 再計算={bundle_hash(bundle)}）"
        )
    knowledge = await kr.resolve_for_project(session, bundle, project_id)
    req = CheckRequest(
        repo_root=repo_root,
        head_sha=manifest.inputs.head_commit,
        base_sha=manifest.inputs.base_commit,
        base_root=base_root,
        limits=manifest.limits,
        fail_on=tuple(manifest.fail_on),  # type: ignore[arg-type]
        mode=manifest.mode,
        run_id=manifest.run_id,
        source_roots=tuple(manifest.source_roots) or (".", "src"),
        today=manifest.today,
        bindings_patch=manifest.bindings_patch or None,
        knowledge=knowledge,
        github_repo_id=project.github_repo_id,
        now=manifest.created_at,
        trusted_root=trusted_root if manifest.trusted_commit is not None else None,
        trusted_sha=manifest.trusted_commit,
    )
    # S70: LLM 支援は記録で再評価する（呼び直さない）。確定は manifest の記録をそのまま渡す
    llm_findings, llm_keys = await _recorded_llm_findings(
        session, req, manifest.llm_assessment_keys, project.team
    )
    req = replace(
        req,
        llm_findings=llm_findings,
        llm_assessment_keys=tuple(llm_keys),
        attestations=tuple(manifest.attestations),
    )
    fresh = run_check(req)
    saved_path = run_dir / DETERMINISTIC_FINDINGS
    if not saved_path.is_file():
        saved_path = run_dir / "findings.json"
    saved = Findings.model_validate(json.loads(saved_path.read_text()))
    diffs, compared, llm = compare_findings(saved, fresh.findings)
    diffs.extend(compare_llm_findings(saved, fresh.findings))
    missing_keys = sorted(set(manifest.llm_assessment_keys) - set(llm_keys))
    diffs.extend(f"LLM 支援の評価の記録が無い（UNKNOWN）: {k}" for k in missing_keys)
    return BundleReplayResult(
        manifest.run_id, bundle.generation, not diffs, compared, diffs, llm, fresh.findings
    )


def compare_llm_findings(saved: Findings, fresh: Findings) -> list[str]:
    """LLM 支援の評価（REVIEW）を指紋（ID）と区分で突き合わせる。記録から作り直すので一致するはず。

    元の run で評価できなかったもの（UNKNOWN）は比べない（記録が無いので再現できない）。
    両方向に比べる: 再評価で増えた評価（保存結果に無い指紋）も差異にする。
    """
    new = {f.id: f for f in fresh.findings if f.assessment_outcome is not None}
    old_ids = {f.id for f in saved.findings if f.assessment_outcome is not None}
    diffs: list[str] = [
        f"{new[fid].requirement_id}（{fid}）: 再評価で LLM 支援の評価が増えた（保存結果に無い）"
        for fid in sorted(new.keys() - old_ids)
    ]
    for f in sorted(
        (f for f in saved.findings if f.assessment_outcome is not None), key=lambda x: x.id
    ):
        got = new.get(f.id)
        if got is None:
            diffs.append(f"{f.requirement_id}（{f.id}）: LLM 支援の評価を記録から再現できない")
        elif got.assessment_outcome != f.assessment_outcome:
            diffs.append(
                f"{f.requirement_id}（{f.id}）: 保存 {f.assessment_outcome}"
                f" → 再評価 {got.assessment_outcome}"
            )
    return diffs


def compare_findings(saved: Findings, fresh: Findings) -> tuple[list[str], int, list[str]]:
    """決定的な Finding を ID で突き合わせる（verdict・category・反例）。欠落・余分も差異。"""
    llm = sorted(f.id for f in saved.findings if _is_llm_assisted(f))
    old = {f.id: f for f in saved.findings if not _is_llm_assisted(f)}
    new = {f.id: f for f in fresh.findings if not _is_llm_assisted(f)}
    diffs: list[str] = []
    for fid in sorted(old.keys() - new.keys()):
        diffs.append(f"{old[fid].requirement_id}（{fid}）: 再評価で出ない")
    for fid in sorted(new.keys() - old.keys()):
        diffs.append(f"{new[fid].requirement_id}（{fid}）: 保存結果に無い")
    for fid in sorted(old.keys() & new.keys()):
        a, b = old[fid], new[fid]
        if (a.verdict, a.category) != (b.verdict, b.category):
            diffs.append(
                f"{a.requirement_id}: 保存 {a.verdict}/{a.category}"
                f" → 再評価 {b.verdict}/{b.category}"
            )
        a_cex = a.counterexample.assignments if a.counterexample else None
        b_cex = b.counterexample.assignments if b.counterexample else None
        if a_cex != b_cex:
            diffs.append(f"{a.requirement_id}: 反例が異なる {a_cex} → {b_cex}")
    return diffs, len(new), llm
