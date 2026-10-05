"""check_pipeline: `speclino check`（S10）と S20 の worker が共有する純粋なオーケストレータ。

PoC 15章 / design/2026-09-22-hackathon-plan.md「判定モデル」の手順を、GitHub・DB・LLM に
依存せず実行する。`repo_root` は解決済みのソーススナップショット（`--head WORKTREE` のときは
作業ツリーそのもの、それ以外は `adapters.localgit.export_snapshot` で展開済みのディレクトリ）
であることを前提にする。git 自体の解決・展開は呼び出し側（CLI／S20 worker）の責務であり、
このモジュールは `head_sha` / `base_sha` を記録用の文字列としてのみ扱う。

手順（PoC 5.1 の必須入力 / hackathon-plan「判定モデル」）:
    (a) `.speclino/domain.yaml` `.speclino/bindings.yaml` `.speclino/contracts/*`、
        `specs/ir/*.spec-ir.json`（behavior ごと）と対応する仕様 Markdown を読む
    (b) validate_spec_ir。error は `CheckInputError`（CLI 終了コード2相当）、warning は
        `CheckResult.validation` に残す
    (b') `base_root` があれば base を先に (a)〜(g) で判定する（S28 意味差分 / S41c 増分解析の
        入力にする）。base が無い・失敗したときは後段の再利用判定は行わない
    (c) `code_ir_override` があればそれを読む（behavior_id 一致を確認）。無ければ、到達関数群の
        ソースハッシュ（`analysis.reachability`）と入力ハッシュ（仕様 rules・domain・bindings・
        contracts）が base と一致する behavior は base の Code IR を再利用し（S41c）、それ以外は
        `analyze_repo` で Code IR を作る。再利用の有無は必ず `RunManifest.analysis` に記録する
        （「解析していない」ことを隠さない）
    (d) `conformance.engine.evaluate` で Findings を作る（複数 behavior は結合する。再利用した
        Code IR でも findings は必ず作り直す）
    (e) `.speclino/policy/*.yaml` があれば VerifierRegistry で実行し Findings に追加する
    (e') base の判定と Finding 同士を突き合わせ、意味差分（PoC 12 章）を作る
    (f) `conformance.gate.aggregate_gate` でゲート判定する
    (g) `RunManifest` を組み立てる（入力ハッシュ・版・上限・fail_on・mode・増分解析の記録）

S65（design/2026-10-05-team-knowledge-registry.md 5 章・8 章）: `CheckRequest.knowledge` があれば、
仕様と規約は run の開始時に固定した bundle だけから読む（リポジトリの `specs/` は読まない）。
    - 仕様は spec の改訂ごとの `content.compiled`（承認時にサーバが作ったもの）と `content.domain`。
      `bindings.yaml`・`contracts/`・`exceptions.yaml`・`.speclino/policy/*.yaml` はリポジトリから
      （D9・D10）、チーム規約は `knowledge.policy`
    - 旧形式（`compiled.legacy`）は (b)〜(d) を spec ごとの domain でそのまま通し、UML の義務は
      構造・順序・包含・遷移の検査（S64）に通す。対応できない・解析できないものは UNKNOWN
    - lock の鮮度（S68、`KNOWLEDGE-LOCK-001`、必須）
    - 出るべき要求 ID（義務・旧形式の要求・規約・lock）を manifest に書き、Finding が欠けたものに
      UNKNOWN（`missing_finding`）を合成する（5.2）
knowledge が None（CLI のローカル実行・自己検証）なら従来どおり Git の入力を読む。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import yaml
import z3
from pydantic import ValidationError

from speclino import __version__
from speclino.analysis.limits import DEFAULT_LIMITS, clamp
from speclino.analysis.path_analyzer import ANALYZER_VERSION, analyze_repo
from speclino.analysis.reachability import reachable_source_hash
from speclino.analysis.structure_check import check_structure
from speclino.conformance.engine import ENGINE_VERSION, EngineConfig, EngineInputError, evaluate
from speclino.conformance.exceptions import (
    RejectedWaiver,
    Waiver,
    rejected_exceptions_for,
    waivers_for,
)
from speclino.conformance.expected import expected_ids, fill_missing, obligation_levels
from speclino.conformance.gate import DEFAULT_FAIL_ON, GatePolicy, GateResult, aggregate_gate
from speclino.conformance.obligation import ObligationContext
from speclino.conformance.semantic_diff import DiffSide, annotate_findings, semantic_diff
from speclino.conformance.state_check import check_state_machine
from speclino.conformance.trace_check import check_trace
from speclino.domain.bindings import Bindings, OperationContract
from speclino.domain.code_ir import (
    AnalysisLimits,
    CodeIR,
    Event,
    ReachabilityDigest,
    StateUpdate,
    UnknownCause,
)
from speclino.domain.code_ir import Path as CodePath
from speclino.domain.common import SnapshotRef, SourceRef
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import CoverageCounts, Finding, Findings, Verdict, VerdictCounts
from speclino.domain.knowledge import ResolvedKnowledge
from speclino.domain.policy import PolicyExceptions, PolicyPackage, PolicyRevision, PolicyRule
from speclino.domain.run import BehaviorAnalysis, RunInputs, RunManifest, RunVersions
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import RuleLevel, SpecIR
from speclino.reporter.json_out import write_artifacts
from speclino.services.knowledge_inputs import OperationResolver, SpecUnit, bundle_hash, spec_units
from speclino.spec import (
    ValidationReport,
    content_hash,
    load_bindings,
    load_contracts,
    load_domain,
    load_spec_ir,
    rules_content_hash,
    validate_spec_ir,
)
from speclino.verifiers.api_conventions import ApiConventionsVerifier
from speclino.verifiers.api_error_format import ApiErrorFormatVerifier
from speclino.verifiers.base import Snapshot
from speclino.verifiers.ci_required import CiRequiredVerifier
from speclino.verifiers.error_message_format import ErrorMessageFormatVerifier
from speclino.verifiers.exceptions import load_policy_exceptions
from speclino.verifiers.lint_preset import LintPresetVerifier
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.naming_identifiers import NamingIdentifiersVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_format import SpecFormatVerifier
from speclino.verifiers.spec_tests import SpecTestsVerifier

Mode = Literal["advisory", "blocking"]


class CheckInputError(ValueError):
    """入力不正・実行不能（CLI では終了コード2）。`conformance.engine.EngineInputError` も同義。"""


@dataclass
class CheckRequest:
    """`run_check` への入力。CLI と S20 worker が組み立てて渡す。"""

    repo_root: Path
    head_sha: str
    base_sha: str | None = None
    project_dir: Path | None = None
    limits: AnalysisLimits = DEFAULT_LIMITS
    fail_on: tuple[Verdict, ...] = ()
    mode: Mode = "advisory"
    code_ir_override: Path | None = None
    run_id: str | None = None
    source_roots: tuple[str, ...] = (".", "src")
    # S18/S20: base との差分照合で承認状態を付け替えた Spec IR（behavior_id → SpecIR）。
    # 指定があれば specs/ir から読んだものの代わりに使う（読込みと検証はそのまま行う）
    spec_overrides: dict[str, SpecIR] | None = None
    # 削除された要求などパイプライン外で作った Finding（ゲートに含める）
    extra_findings: tuple[Finding, ...] = ()
    # 例外の期限判定に使う日付（テストで固定できる）
    today: date | None = None
    # 人の回答（human_ask）による run 限定の操作束縛: 操作 ID → 実装シンボル（S32）
    bindings_patch: dict[str, str] | None = None
    # S28: base スナップショット。指定があれば「base 仕様 + base コード」でも判定し、head の
    # Findings と突き合わせて意味差分（新規 / 既存 / 解消 / 判定不能化）を作る
    base_root: Path | None = None
    base_project_dir: Path | None = None
    # S42: 適用するチーム規約の改訂。DB から取るのは呼び出し側（配線の層）の責務で、
    # このモジュールはデータとして受け取るだけで DB には依存しない。None なら従来どおり
    # プロジェクト固有の .speclino/policy/*.yaml だけを実行する
    team_policy: PolicyRevision | None = None
    # S65: run の開始時に固定した bundle をこのプロジェクトに当てた結果。あれば仕様とチーム規約は
    # ここからだけ読み（team_policy は無視する）、無ければ従来の Git の入力（CLI・自己検証）
    knowledge: ResolvedKnowledge | None = None
    # S65: lock の照合に使うリポジトリ ID（knowledge があるときだけ使う。None なら lock は UNKNOWN）
    github_repo_id: int | None = None
    # run の時刻（manifest の created_at と lock の期限の判定）。replay は記録の時刻を渡す
    now: datetime | None = None

    @property
    def resolved_project_dir(self) -> Path:
        """`.speclino/` と `specs/` の在処。未指定なら `repo_root` 直下。"""
        return self.project_dir if self.project_dir is not None else self.repo_root


@dataclass
class CheckResult:
    manifest: RunManifest
    spec_irs: list[SpecIR]
    code_irs: list[CodeIR]
    findings: Findings
    gate: GateResult
    validation: list[ValidationReport]
    policy_findings: list[Finding]
    # ゲートを決めた入力。エージェントの証拠で Findings が変わった後に再計算するとき、
    # 同じ level と免除で判定するために持ち回る（別の場所で作り直すと、規約の level が
    # 仕様側に上書きされる・免除が消えるといったずれが起きる。codex レビュー指摘）。
    gate_levels: dict[str, RuleLevel] = field(default_factory=dict)
    waived_finding_ids: frozenset[str] = field(default_factory=frozenset)
    # 免除の中身（どの例外で・いつまで・どう直すか）。gate.json に残す
    waivers: list[Waiver] = field(default_factory=list)
    artifacts: dict[str, Path] | None = None
    domain: DomainSpec | None = None
    bindings: Bindings | None = None
    contracts: dict[str, OperationContract] = field(default_factory=dict)
    semantic_diff: SemanticDiff | None = None
    # 意味差分の base 側（エージェントの再解析後に差分を取り直すために保持する）
    base_side: DiffSide | None = None
    base_error: str | None = None


def run_check(req: CheckRequest) -> CheckResult:
    return _run_check(req, is_base=False)


@dataclass
class _LegacyUnit:
    """旧形式の Spec IR 1 つと、それを判定する domain（Git では共通、bundle では spec ごと）。"""

    spec: SpecIR
    domain: DomainSpec
    domain_hash: str


@dataclass
class _Inputs:
    units: list[_LegacyUnit]
    bindings: Bindings
    bindings_hash: str | None
    contracts: dict[str, OperationContract]
    contracts_hash: str | None
    # CheckResult.domain（エージェントと domain.json 用）。bundle で spec ごとに違えば None
    domain: DomainSpec | None
    domain_hash: str | None
    validations: list[ValidationReport] = field(default_factory=list)
    spec_markdown_texts: list[str] = field(default_factory=list)
    spec_ir_texts: list[str] = field(default_factory=list)
    spec_units: list[SpecUnit] = field(default_factory=list)


def _run_check(req: CheckRequest, *, is_base: bool) -> CheckResult:
    project_dir = req.resolved_project_dir
    limits = clamp(req.limits)
    created_at = req.now or datetime.now(UTC)
    knowledge = req.knowledge
    inputs = (
        _git_inputs(req, project_dir)
        if knowledge is None
        else _bundle_inputs(req, project_dir, knowledge)
    )

    error_messages = [
        f"{report.spec.behavior_id}: [{d.code}] {d.rule_id or '-'} {d.path or ''} {d.message}"
        for report in inputs.validations
        for d in report.errors
    ]
    if error_messages:
        raise CheckInputError("Spec IR の検証エラー:\n" + "\n".join(error_messages))

    snapshot = SnapshotRef(
        snapshot_id=req.head_sha,
        repository=str(req.repo_root),
        commit=None if req.head_sha == "WORKTREE" else req.head_sha,
    )
    spec_irs = [u.spec for u in inputs.units]
    bindings, contracts = inputs.bindings, inputs.contracts

    override, override_text = _load_override(req.code_ir_override, spec_irs)
    run_id = req.run_id or str(uuid4())

    # S28/S41c: base があれば先に判定する。head の再利用判定（到達ハッシュ・入力ハッシュの
    # 比較）と意味差分の両方が base の Code IR / Findings を必要とするため
    base_result: CheckResult | None = None
    base_error: str | None = None
    if req.base_root is not None:
        base_result, base_error = _run_base(req, run_id)
    # 再利用時に実際の Code IR を取り出す索引（判定自体は _decide_reuse が base_result を見る）
    base_code_by_id = (
        {c.behavior_id: c for c in base_result.code_irs} if base_result is not None else {}
    )

    code_irs: list[CodeIR] = []
    analysis_records: list[BehaviorAnalysis] = []
    for unit in inputs.units:
        spec = unit.spec
        # 到達関数群のハッシュは override 時も含め常に計算する（再利用の判定材料にしないときも
        # manifest に残し、「解析していない」ことを隠さない）
        digest = reachable_source_hash(req.repo_root, spec.behavior_id, bindings, req.source_roots)
        inputs_hash = _inputs_hash(
            rules_content_hash(spec.rules),
            unit.domain_hash,
            inputs.bindings_hash or "",
            inputs.contracts_hash,
        )
        if override is not None and override.behavior_id == spec.behavior_id:
            code_irs.append(override)
            analysis_records.append(
                _analysis_record(spec.behavior_id, digest, inputs_hash, False, "override")
            )
            continue
        reused, reason = _decide_reuse(
            req, spec.behavior_id, digest, inputs_hash, base_result, base_error
        )
        if reused:
            code = _rebase_snapshot(base_code_by_id[spec.behavior_id], snapshot)
        else:
            code = analyze_repo(
                req.repo_root,
                spec.behavior_id,
                unit.domain,
                bindings,
                contracts,
                limits,
                snapshot,
                req.source_roots,
            )
        code_irs.append(code)
        analysis_records.append(
            _analysis_record(spec.behavior_id, digest, inputs_hash, reused, reason)
        )

    engine_config = EngineConfig(solver_timeout_ms=limits.solver_timeout_ms)
    # 再利用した Code IR でも findings は必ず作り直す（finding id の安定と判定の決定性のため。
    # 省くのは analyze_repo（静的解析）だけ）
    findings_parts = [
        evaluate(unit.spec, code, unit.domain, engine_config, run_id)
        for unit, code in zip(inputs.units, code_irs, strict=True)
    ]
    combined = _merge_findings(findings_parts, run_id)
    levels: dict[str, RuleLevel] = {rule.id: rule.level for spec in spec_irs for rule in spec.rules}

    team_policy = knowledge.policy if knowledge is not None else req.team_policy
    policy_result = _run_policies(project_dir, team_policy, tolerate_errors=knowledge is not None)
    knowledge_findings: list[Finding] = []
    if knowledge is not None:
        codes = {
            (unit.spec.behavior_id, unit.domain_hash): code
            for unit, code in zip(inputs.units, code_irs, strict=True)
        }
        uml_findings, uml_levels = _uml_findings(
            req, inputs, limits, snapshot, engine_config, codes
        )
        knowledge_findings.extend(uml_findings)
        levels.update(uml_levels)
        if not is_base:
            knowledge_findings.append(_lock_finding(req, knowledge, created_at))
            notice = _git_specs_notice(project_dir, inputs)
            if notice is not None:
                knowledge_findings.append(notice)
    combined = _with_extra_findings(
        combined, [*knowledge_findings, *policy_result.findings, *req.extra_findings]
    )
    levels.update(policy_result.levels)

    expected: list[str] = []
    if knowledge is not None:
        combined, expected = _fill_expected(combined, inputs, policy_result, levels, is_base)

    # S28: base の判定と Finding 同士を突き合わせる（head を base 仕様に照合しない）
    diff: SemanticDiff | None = None
    base_side: DiffSide | None = None
    if req.base_root is not None:
        base_side = (
            DiffSide(req.base_sha, base_result.spec_irs, base_result.code_irs, base_result.findings)
            if base_result is not None
            else None
        )
        head_side = DiffSide(req.head_sha, spec_irs, code_irs, combined)
        diff = semantic_diff(base_side, head_side, base_error)
        combined = annotate_findings(combined, diff)

    fail_on = req.fail_on or DEFAULT_FAIL_ON
    # 例外（免除）: 有効期限内で対象が一致する VIOLATED だけ。適合には書き換えず別記する（AC-009）。
    # exceptions_allowed=False のルール（チーム規約 + プロジェクト固有の規約の両方）は
    # policy_result.rules_by_id に載っているので、対象・期限が一致していても免除しない。
    exceptions = _load_exceptions(project_dir)
    today = req.today or date.today()
    waivers = waivers_for(combined, exceptions, today, policy_result.rules_by_id)
    waived = frozenset(w.finding_id for w in waivers)
    rejected = rejected_exceptions_for(combined, exceptions, today, policy_result.rules_by_id)
    combined = _annotate_rejected_waivers(combined, rejected)
    gate = aggregate_gate(combined, levels, GatePolicy(fail_on=fail_on, mode=req.mode), waived)

    compiled_versions = sorted(
        {u.compiled.parser_version for u in inputs.spec_units if u.compiled is not None}
    )
    manifest = RunManifest(
        run_id=run_id,
        created_at=created_at,
        inputs=RunInputs(
            repository=str(req.repo_root),
            head_commit=req.head_sha,
            base_commit=req.base_sha,
            spec_content_hash=_combined_hash(inputs.spec_markdown_texts),
            spec_ir_hash=_combined_hash(inputs.spec_ir_texts),
            bindings_hash=inputs.bindings_hash,
            domain_hash=inputs.domain_hash,
            contracts_hash=inputs.contracts_hash,
            policy_hash=_combined_hash(policy_result.texts),
            team_policy_hash=policy_result.team_policy_hash,
            code_ir_hash=content_hash(override_text) if override_text is not None else None,
        ),
        versions=RunVersions(
            speclino=__version__,
            analyzer=ANALYZER_VERSION,
            engine=ENGINE_VERSION,
            solver=z3.get_version_string(),
        ),
        limits=limits,
        fail_on=list(fail_on),
        mode=req.mode,
        policy_team=team_policy.team if team_policy is not None else None,
        policy_revision=team_policy.revision if team_policy is not None else None,
        analysis=analysis_records,
        bundle_generation=knowledge.bundle.generation if knowledge is not None else None,
        bundle_hash=bundle_hash(knowledge.bundle) if knowledge is not None else None,
        uml_parser_version=",".join(compiled_versions) or None,
        expected_requirement_ids=expected,
    )

    return CheckResult(
        manifest=manifest,
        spec_irs=spec_irs,
        code_irs=code_irs,
        findings=combined,
        gate=gate,
        validation=inputs.validations,
        policy_findings=policy_result.findings,
        gate_levels=levels,
        waived_finding_ids=waived,
        waivers=waivers,
        domain=inputs.domain,
        bindings=bindings,
        contracts=contracts,
        semantic_diff=diff,
        base_side=base_side,
        base_error=base_error,
    )


def _patched_bindings(bindings: Bindings, patch: dict[str, str] | None) -> Bindings:
    if not patch:
        return bindings
    ops = {
        op_id: (ob.model_copy(update={"symbol": patch[op_id]}) if op_id in patch else ob)
        for op_id, ob in bindings.operations.items()
    }
    return bindings.model_copy(update={"operations": ops})


def _git_inputs(req: CheckRequest, project_dir: Path) -> _Inputs:
    """従来の入力: `.speclino/` と `specs/ir/*.spec-ir.json`・`specs/*.md`（Git）。"""
    domain_path = project_dir / ".speclino" / "domain.yaml"
    bindings_path = project_dir / ".speclino" / "bindings.yaml"
    if not domain_path.is_file():
        raise CheckInputError(f"domain.yaml が無い: {domain_path}")
    if not bindings_path.is_file():
        raise CheckInputError(f"bindings.yaml が無い: {bindings_path}")
    domain_text = domain_path.read_text(encoding="utf-8")
    bindings_text = bindings_path.read_text(encoding="utf-8")
    domain = load_domain(domain_path)
    bindings = _patched_bindings(load_bindings(bindings_path), req.bindings_patch)
    contracts_dir = project_dir / ".speclino" / "contracts"
    contracts = load_contracts(contracts_dir) if contracts_dir.is_dir() else {}
    # S41c: 増分解析の入力同一性（inputs_hash）と RunInputs の両方でこの3つを使い回す
    domain_hash = content_hash(domain_text)

    spec_ir_dir = project_dir / "specs" / "ir"
    spec_ir_files = sorted(spec_ir_dir.glob("*.spec-ir.json")) if spec_ir_dir.is_dir() else []
    if not spec_ir_files:
        raise CheckInputError(f"Spec IR が無い: {spec_ir_dir}/*.spec-ir.json")

    inputs = _Inputs(
        units=[],
        bindings=bindings,
        bindings_hash=content_hash(bindings_text),
        contracts=contracts,
        contracts_hash=_combined_hash(_contract_texts(contracts_dir)),
        domain=domain,
        domain_hash=domain_hash,
    )
    for path in spec_ir_files:
        raw_text = path.read_text(encoding="utf-8")
        inputs.spec_ir_texts.append(raw_text)
        spec = load_spec_ir(path)
        if req.spec_overrides and spec.behavior_id in req.spec_overrides:
            spec = req.spec_overrides[spec.behavior_id]
        inputs.units.append(_LegacyUnit(spec, domain, domain_hash))
        markdown_text = _find_spec_markdown(project_dir, spec)
        if markdown_text is not None:
            inputs.spec_markdown_texts.append(markdown_text)
        inputs.validations.append(validate_spec_ir(spec, domain, bindings, markdown_text))
    return inputs


def _canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _bundle_inputs(req: CheckRequest, project_dir: Path, knowledge: ResolvedKnowledge) -> _Inputs:
    """bundle の入力: 仕様は spec の改訂の compiled と domain、対応・契約はリポジトリから（D9）。

    `specs/` は読まない。bindings.yaml が無ければ空（図の要素はどれも対応なし → UNKNOWN）。
    """
    bindings_path = project_dir / ".speclino" / "bindings.yaml"
    if bindings_path.is_file():
        bindings_text: str | None = bindings_path.read_text(encoding="utf-8")
        bindings = load_bindings(bindings_path)
    else:
        bindings_text, bindings = None, Bindings()
    bindings = _patched_bindings(bindings, req.bindings_patch)
    contracts_dir = project_dir / ".speclino" / "contracts"
    contracts = load_contracts(contracts_dir) if contracts_dir.is_dir() else {}

    units = spec_units(knowledge)
    inputs = _Inputs(
        units=[],
        bindings=bindings,
        bindings_hash=content_hash(bindings_text) if bindings_text is not None else None,
        contracts=contracts,
        contracts_hash=_combined_hash(_contract_texts(contracts_dir)),
        domain=None,
        domain_hash=None,
        spec_units=units,
    )
    domain_texts: dict[str, DomainSpec] = {}
    for unit in units:
        inputs.spec_markdown_texts.append(unit.markdown)
        if unit.compiled is None:
            continue  # _uml_findings が UNKNOWN にする
        domain = unit.domain or DomainSpec()
        domain_text = _canonical(domain.model_dump(mode="json"))
        domain_hash = content_hash(domain_text)
        domain_texts[domain_text] = domain
        inputs.spec_ir_texts.append(_canonical(unit.compiled.model_dump(mode="json")))
        for spec in unit.compiled.legacy:
            inputs.units.append(_LegacyUnit(spec, domain, domain_hash))
            inputs.validations.append(validate_spec_ir(spec, domain, bindings, unit.markdown))
    if domain_texts:
        inputs.domain_hash = _combined_hash(list(domain_texts))
        # エージェントは 1 つの domain で再解析する。spec ごとに違えば渡さない（調査しない）
        if len(domain_texts) == 1:
            inputs.domain = next(iter(domain_texts.values()))
    return inputs


def _uml_findings(
    req: CheckRequest,
    inputs: _Inputs,
    limits: AnalysisLimits,
    snapshot: SnapshotRef,
    config: EngineConfig,
    codes: dict[tuple[str, str], CodeIR],
) -> tuple[list[Finding], dict[str, RuleLevel]]:
    """UML の義務（構造・順序・包含・遷移）を S64 の検査に通す。

    - 順序・包含: 入口の操作 → behavior（`OperationResolver.behavior`）。無ければ UNKNOWN
    - 遷移: 入口を持たないので bindings の全 behavior の Code IR で検査する（behavior ごとに 1 つ）
    - Code IR は (behavior, domain) ごとに 1 回だけ作る。解析の例外はその義務を UNKNOWN にする
    - 検査器の例外も UNKNOWN（黙って落とさない。偽の VERIFIED にしない）
    """
    resolver = OperationResolver(inputs.bindings)
    findings: list[Finding] = []
    levels: dict[str, RuleLevel] = {}
    failures: dict[tuple[str, str], str] = {}

    def code_for(behavior_id: str, domain: DomainSpec, domain_hash: str) -> CodeIR | str:
        key = (behavior_id, domain_hash)
        if key in codes:
            return codes[key]
        if key in failures:
            return failures[key]
        try:
            code = analyze_repo(
                req.repo_root,
                behavior_id,
                domain,
                inputs.bindings,
                inputs.contracts,
                limits,
                snapshot,
                req.source_roots,
            )
        except Exception as e:  # noqa: BLE001 - 解析できないものは UNKNOWN にする
            failures[key] = f"{type(e).__name__}: {e}"
            return failures[key]
        codes[key] = code
        return code

    def guarded(ctx: ObligationContext, check: Callable[[], list[Finding]]) -> list[Finding]:
        try:
            return check()
        except Exception as e:  # noqa: BLE001 - 検査器の例外は UNKNOWN（設計 5.2）
            return [ctx.unknown(f"検査器の例外で判定できない（{type(e).__name__}: {e}）")]

    for unit in inputs.spec_units:
        compiled = unit.compiled
        if compiled is None:
            ctx = ObligationContext(
                f"spec:{unit.name}", f"spec:{unit.name}", "mandatory", "command_invocation"
            )
            findings.append(
                ctx.unknown(
                    f"承認済みの仕様 {unit.name}@{unit.revision} に変換結果（compiled）が無い",
                    ["仕様を承認し直す"],
                )
            )
            continue
        levels.update(obligation_levels(compiled))
        domain = unit.domain or DomainSpec()
        domain_hash = content_hash(_canonical(domain.model_dump(mode="json")))
        for st in compiled.structure:
            ctx = ObligationContext(st.id, st.id.split("#", 1)[0], st.level, "command_invocation")
            findings.extend(
                guarded(ctx, partial(check_structure, st, req.repo_root, req.source_roots))
            )
        for tr in compiled.traces:
            behavior_id = resolver.behavior(tr.entry_operation)
            ctx = ObligationContext(
                tr.id, behavior_id or tr.id.split("#", 1)[0], tr.level, "command_invocation"
            )
            if behavior_id is None:
                findings.append(
                    ctx.unknown(
                        f"入口の操作 {tr.entry_operation} に対応する振る舞いが無い（bindings.yaml"
                        " の behaviors に同名のキーも、entrypoint の名前の一致も無い）",
                        ["bindings.yaml の behaviors に入口の操作名をキーにした対応を書く"],
                    )
                )
                continue
            code = code_for(behavior_id, domain, domain_hash)
            if isinstance(code, str):
                findings.append(ctx.unknown(f"振る舞い {behavior_id} を解析できない（{code}）"))
                continue
            findings.extend(
                guarded(
                    ctx,
                    partial(
                        check_trace,
                        tr,
                        code,
                        config,
                        domain=domain,
                        resolve_operation=resolver.operation,
                        known_operations=resolver.known_operations([code]),
                    ),
                )
            )
        for sm in compiled.state_machines:
            for behavior_id in sorted(inputs.bindings.behaviors):
                ctx = ObligationContext(sm.id, behavior_id, sm.level, "memory_state")
                code = code_for(behavior_id, domain, domain_hash)
                if isinstance(code, str):
                    findings.append(ctx.unknown(f"振る舞い {behavior_id} を解析できない（{code}）"))
                    continue
                findings.extend(
                    guarded(
                        ctx,
                        partial(check_state_machine, sm, code, config, domain=domain),
                    )
                )
    return findings, levels


def _lock_finding(req: CheckRequest, knowledge: ResolvedKnowledge, now: datetime) -> Finding:
    """PR の head の `.speclino/lock.json` の鮮度（S68、必須）。repo ID が無ければ UNKNOWN。"""
    # knowledge_sync → knowledge_registry → check_pipeline の循環を避けて関数内で import する
    from speclino.services import knowledge_sync

    if req.github_repo_id is None:
        return Finding(
            id=f"knowledge:{knowledge_sync.LOCK_REQUIREMENT_ID}",
            requirement_id=knowledge_sync.LOCK_REQUIREMENT_ID,
            behavior_id="knowledge:lock",
            verdict="UNKNOWN",
            category="unsupported_analysis",
            severity="high",
            message="プロジェクトのリポジトリ ID が未登録のため、lock の持ち主を照合できない",
            expected="lock のリポジトリ ID がこのプロジェクトのものと一致すること",
            actual="リポジトリ ID なし",
            claim_scope="persisted_state",
            code_refs=[SourceRef(snapshot_id=req.head_sha, path=knowledge_sync.LOCK_PATH)],
            next_steps=["speclino workspace backfill-repo-ids でリポジトリ ID を埋める"],
        )
    snapshot = DirectorySnapshot(root=req.repo_root, snapshot_id=req.head_sha)
    return knowledge_sync.check_lock_snapshot(
        cast(Snapshot, snapshot),
        current=knowledge.bundle,
        current_members=knowledge_sync.project_members(knowledge),
        github_repo_id=req.github_repo_id,
        now=now,
    )


GIT_SPECS_REQUIREMENT_ID = "KNOWLEDGE-SPEC-001"


def _git_specs_notice(project_dir: Path, inputs: _Inputs) -> Finding | None:
    """bundle に仕様が 1 つも無いのにリポジトリに旧形式の仕様がある → UNKNOWN（取り込み待ち）。

    bundle の run は `specs/` を読まない。取り込み前のリポジトリで仕様が黙って外れないようにする。
    """
    spec_ir_dir = project_dir / "specs" / "ir"
    if inputs.spec_units or not (spec_ir_dir.is_dir() and any(spec_ir_dir.glob("*.spec-ir.json"))):
        return None
    return Finding(
        id=f"knowledge:{GIT_SPECS_REQUIREMENT_ID}",
        requirement_id=GIT_SPECS_REQUIREMENT_ID,
        behavior_id="knowledge:specs",
        verdict="UNKNOWN",
        category="unsupported_analysis",
        severity="high",
        message=(
            "リポジトリの specs/ir に仕様があるが、ワークスペースの承認済みの仕様に無い。"
            "仕様の正本は SpecLino にあるため、この run は検査していない"
        ),
        claim_scope="command_invocation",
        code_refs=[SourceRef(snapshot_id="specs", path="specs/ir")],
        next_steps=["speclino import-specs で取り込み、承認する"],
    )


def _fill_expected(
    findings: Findings,
    inputs: _Inputs,
    policy_result: _PolicyRunResult,
    levels: dict[str, RuleLevel],
    is_base: bool,
) -> tuple[Findings, list[str]]:
    """出るべき要求 ID を列挙し、Finding が欠けたものを UNKNOWN で補う（設計 5.2）。

    規約の Finding は `<rule>:<path>` でも出るので、その接頭辞があれば出たとみなす。補った規約の
    ID は levels にルールの level を入れる（無いと mandatory 扱い）。
    """
    from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID

    expected: list[str] = []
    for unit in inputs.spec_units:
        if unit.compiled is None:
            continue
        spec_ids = expected_ids(unit.compiled, [])
        expected.extend(spec_ids)
        findings = fill_missing(findings, spec_ids, f"spec:{unit.name}")
    present = [f.requirement_id for f in findings.findings]
    missing_policy = [
        rid
        for rid in policy_result.expected_rule_ids
        if not any(r == rid or r.startswith(rid + ":") for r in present)
    ]
    expected.extend(policy_result.expected_rule_ids)
    findings = fill_missing(findings, missing_policy, "policy:missing")
    for rid in missing_policy:
        levels[rid] = policy_result.rules_by_id[rid].level
    if not is_base:
        expected.append(LOCK_REQUIREMENT_ID)
    return findings, list(dict.fromkeys(expected))


def rediff(result: CheckResult, findings: Findings) -> tuple[SemanticDiff | None, Findings]:
    """エージェントの証拠で head の Findings が変わった後に、同じ base で意味差分を取り直す。"""
    if result.semantic_diff is None:
        return None, findings
    head = DiffSide(result.manifest.inputs.head_commit, result.spec_irs, result.code_irs, findings)
    diff = semantic_diff(result.base_side, head, result.base_error)
    return diff, annotate_findings(findings, diff)


def write_result(result: CheckResult, out_dir: Path) -> dict[str, Path]:
    """成果物一式を `out_dir` に書く（`reporter.write_artifacts` の薄いラッパー）。

    behavior が1件なら `write_artifacts` の出力（spec-ir.json / code-ir.json）をそのまま使う。
    0 件（S65 の bundle の run で旧形式の仕様が無い）なら spec-ir.json を書かない。
    複数 behavior のときは、結合済み Findings で run-manifest.json / findings.json / gate.json /
    review.md を1組だけ書き、spec-ir / code-ir は behavior ごとに
    `spec-ir.<behavior_id>.json` / `code-ir.<behavior_id>.json` を追加で書く。
    """
    if len(result.spec_irs) == 1:
        code = result.code_irs[0] if result.code_irs else None
        written = write_artifacts(
            out_dir,
            result.manifest,
            result.spec_irs[0],
            code,
            result.findings,
            result.gate,
            result.semantic_diff,
            result.waivers,
        )
        _write_domain(result, out_dir, written)
        return written

    written = write_artifacts(
        out_dir,
        result.manifest,
        result.spec_irs[0] if result.spec_irs else None,
        None,
        result.findings,
        result.gate,
        result.semantic_diff,
        result.waivers,
    )
    _write_domain(result, out_dir, written)
    for spec in result.spec_irs:
        name = f"spec-ir.{spec.behavior_id}.json"
        path = out_dir / name
        _write_json(path, spec.model_dump(mode="json"))
        written[name] = path
    for code in result.code_irs:
        name = f"code-ir.{code.behavior_id}.json"
        path = out_dir / name
        _write_json(path, code.model_dump(mode="json"))
        written[name] = path
    return written


# ---------------------------------------------------------------------- internals


def _run_base(req: CheckRequest, run_id: str) -> tuple[CheckResult | None, str | None]:
    """base スナップショットを head と同じ手順（run_check）で判定する。

    base は信頼済み（デフォルトブランチ）なので承認の付け替え・削除 Finding・回答による束縛は
    適用せず、base のファイルどおりに判定する（`bindings_patch` も引き継がない）。base 側が
    判定できない（初回導入で仕様が無い等）ときは (None, 理由) を返し、呼び出し側は head の
    判定を意味差分の「追加」として並べ、S41c の再利用判定も行わない。
    """
    assert req.base_root is not None
    base_req = CheckRequest(
        repo_root=req.base_root,
        head_sha=req.base_sha or "BASE",
        project_dir=req.base_project_dir,
        limits=req.limits,
        run_id=f"{run_id}-base",
        source_roots=req.source_roots,
        today=req.today,
        # S65: base も同じ bundle で判定する（意味差分は同じ仕様どうしで比べる）。lock は head だけ
        knowledge=req.knowledge,
        github_repo_id=req.github_repo_id,
        now=req.now,
    )
    try:
        return _run_check(base_req, is_base=True), None
    except (CheckInputError, EngineInputError, ValidationError, ValueError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def _inputs_hash(
    rules_hash: str, domain_hash: str, bindings_hash: str, contracts_hash: str | None
) -> str:
    """S41c: 再利用可否を決める「仕様側の入力」の同一性ハッシュ（rules・domain・bindings・
    contracts）。base と head でこれが一致しないときは到達ハッシュが一致していても再利用しない。
    """
    payload = {
        "rules": rules_hash,
        "domain": domain_hash,
        "bindings": bindings_hash,
        "contracts": contracts_hash or "",
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return content_hash(canonical)


def _analysis_record(
    behavior_id: str,
    digest: ReachabilityDigest,
    inputs_hash: str,
    reused: bool,
    reason: str,
) -> BehaviorAnalysis:
    return BehaviorAnalysis(
        behavior_id=behavior_id,
        reachability=digest,
        inputs_hash=inputs_hash,
        reused_from_base=reused,
        reason=reason,
    )


def _decide_reuse(
    req: CheckRequest,
    behavior_id: str,
    digest: ReachabilityDigest,
    inputs_hash: str,
    base_result: CheckResult | None,
    base_error: str | None,
) -> tuple[bool, str]:
    """S41c: base の Code IR を再利用してよいか（到達ハッシュと入力ハッシュの両方が一致するか）。

    `bindings_patch`（S32 の回答による束縛）は base 側には適用されないため、束縛が付いた run は
    安全側に倒して再利用しない。`base_result` が None なのは、base 無し（`req.base_root is
    None`）か base の解析自体が失敗した（理由は `base_error`）かのどちらか。
    """
    if req.bindings_patch:
        return False, "bindings_patch あり"
    if req.base_root is None:
        return False, "base 無し"
    if base_result is None:
        return False, f"base の解析が失敗: {base_error}" if base_error else "base の解析が失敗"
    base_analysis = next(
        (a for a in base_result.manifest.analysis if a.behavior_id == behavior_id), None
    )
    if base_analysis is None:
        return False, "base に無い behavior"
    if base_analysis.reachability.source_hash != digest.source_hash:
        return False, "reachable ハッシュが base と不一致"
    if base_analysis.inputs_hash != inputs_hash:
        return False, "inputs ハッシュが base と不一致"
    return True, "reachable と inputs が base と一致"


def _rebase_ref(ref: SourceRef | None, snapshot_id: str) -> SourceRef | None:
    return ref.model_copy(update={"snapshot_id": snapshot_id}) if ref is not None else None


def _rebase_refs[T: (StateUpdate, Event, UnknownCause)](
    items: list[T], snapshot_id: str
) -> list[T]:
    """`source_ref` を1つ持つ IR 要素のリストの snapshot_id を揃える。

    `Path.state_updates` / `Path.events` / `Path.unknown_causes` / `CodeIR.unknown_causes` で
    共通の形をまとめる。
    """
    return [
        item.model_copy(update={"source_ref": _rebase_ref(item.source_ref, snapshot_id)})
        for item in items
    ]


def _rebase_path(path: CodePath, snapshot_id: str) -> CodePath:
    return path.model_copy(
        update={
            "state_updates": _rebase_refs(path.state_updates, snapshot_id),
            "events": _rebase_refs(path.events, snapshot_id),
            "outcome": path.outcome.model_copy(
                update={"source_ref": _rebase_ref(path.outcome.source_ref, snapshot_id)}
            ),
            "unknown_causes": _rebase_refs(path.unknown_causes, snapshot_id),
            "source_refs": [
                r.model_copy(update={"snapshot_id": snapshot_id}) for r in path.source_refs
            ],
        }
    )


def _rebase_snapshot(code: CodeIR, snapshot: SnapshotRef) -> CodeIR:
    """S41c: 再利用する base の Code IR の snapshot を head のものに差し替える。

    トップレベルの `snapshot` だけでなく、経路内の各 `source_ref.snapshot_id` も揃える。
    揃えないと、findings の `code_refs` に base の snapshot_id（base_sha）が残ってしまい、
    「再利用の有無で findings が変わらない」（同じ入力 → 同じ結果）という決定性が、
    base_sha と head_sha が異なる通常のケースで壊れる。symbol / path / line 等の中身は変えない
    （どのファイルのどこかという内容は base のまま。「どの run の結果として見せるか」という
    識別子だけを揃える）。
    """
    sid = snapshot.snapshot_id
    return code.model_copy(
        update={
            "snapshot": snapshot,
            "paths": [_rebase_path(p, sid) for p in code.paths],
            "unknown_causes": _rebase_refs(code.unknown_causes, sid),
        }
    )


def _write_domain(result: CheckResult, out_dir: Path, written: dict[str, Path]) -> None:
    """replay（AC-013）に必要なドメイン定義も凍結する。"""
    if result.domain is None:
        return
    path = out_dir / "domain.json"
    _write_json(path, result.domain.model_dump(mode="json"))
    written["domain.json"] = path


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def _load_override(
    code_ir_override: Path | None, spec_irs: list[SpecIR]
) -> tuple[CodeIR | None, str | None]:
    if code_ir_override is None:
        return None, None
    text = code_ir_override.read_text(encoding="utf-8")
    override = CodeIR.model_validate(json.loads(text))
    if override.behavior_id not in {s.behavior_id for s in spec_irs}:
        raise CheckInputError(f"--code-ir の behavior_id が Spec IR に無い: {override.behavior_id}")
    return override, text


def _find_spec_markdown(project_dir: Path, spec: SpecIR) -> str | None:
    """`specs/*.md` から、この Spec IR の requirement_id を含む最初のファイルを読む。

    見つからなければ None（呼び出し側は spec_content_hash の照合をスキップする）。
    """
    requirement_ids = {
        ref.requirement_id for rule in spec.rules for ref in rule.source_refs if ref.requirement_id
    }
    if not requirement_ids:
        return None
    specs_dir = project_dir / "specs"
    if not specs_dir.is_dir():
        return None
    for md_path in sorted(specs_dir.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        if any(rid in text for rid in requirement_ids):
            return text
    return None


def _combined_hash(parts: list[str]) -> str | None:
    """複数入力を安定した順序で連結したハッシュ（1件なら単体のハッシュと一致する）。"""
    if not parts:
        return None
    return content_hash("\x1e".join(sorted(parts)))


def _contract_texts(contracts_dir: Path) -> list[str]:
    if not contracts_dir.is_dir():
        return []
    return [
        file.read_text(encoding="utf-8")
        for file in sorted(contracts_dir.iterdir())
        if file.suffix in (".yaml", ".yml", ".json")
    ]


def _merge_findings(parts: list[Findings], run_id: str | None) -> Findings:
    """複数 behavior の Findings を1件に結合し、counts / coverage を合算し直す。"""
    all_findings = [f for part in parts for f in part.findings]
    counts = VerdictCounts()
    for part in parts:
        for v in ("VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW"):
            counts = counts.model_copy(update={v: getattr(counts, v) + getattr(part.counts, v)})
    coverage = CoverageCounts(
        requirements_total=sum(p.coverage.requirements_total for p in parts),
        requirements_approved=sum(p.coverage.requirements_approved for p in parts),
        requirements_in_scope=sum(p.coverage.requirements_in_scope for p in parts),
        requirements_decided=sum(p.coverage.requirements_decided for p in parts),
        requirements_unanalyzed=sum(p.coverage.requirements_unanalyzed for p in parts),
    )
    return Findings(run_id=run_id, findings=all_findings, counts=counts, coverage=coverage)


def _with_extra_findings(base: Findings, extra: list[Finding]) -> Findings:
    if not extra:
        return base
    all_findings = [*base.findings, *extra]
    counts = VerdictCounts()
    for f in all_findings:
        counts = counts.model_copy(update={f.verdict: getattr(counts, f.verdict) + 1})
    return base.model_copy(update={"findings": all_findings, "counts": counts})


def _policy_registry() -> VerifierRegistry:
    registry = VerifierRegistry()
    registry.register(MakeTargetsVerifier())
    registry.register(SpecFormatVerifier())
    registry.register(SpecTestsVerifier())
    registry.register(CiRequiredVerifier())
    registry.register(ApiErrorFormatVerifier())
    registry.register(ApiConventionsVerifier())
    registry.register(NamingIdentifiersVerifier())
    registry.register(ErrorMessageFormatVerifier())
    registry.register(LintPresetVerifier())
    return registry


def _policy_levels(package: PolicyPackage, findings: list[Finding]) -> dict[str, RuleLevel]:
    """Finding.requirement_id は `<rule.id>` または `<rule.id>:<path>`。rule.level から引く。"""
    base = {rule.id: rule.level for rule in package.rules}
    levels: dict[str, RuleLevel] = {}
    for f in findings:
        rule_id = f.requirement_id.split(":", 1)[0]
        level = base.get(rule_id)
        if level is not None:
            levels[f.requirement_id] = level
    return levels


def _load_exceptions(project_dir: Path) -> PolicyExceptions:
    exceptions_path = project_dir / ".speclino" / "exceptions.yaml"
    if not exceptions_path.is_file():
        return PolicyExceptions(exceptions=[])
    return load_policy_exceptions(yaml.safe_load(exceptions_path.read_text(encoding="utf-8")) or {})


def policy_package_hash(package: PolicyPackage) -> str:
    """規約パッケージの内容だけから決定的なハッシュを作る（S42）。

    `_run_policies` がチーム規約から `RunInputs.team_policy_hash` を作るのに使い、
    `replay_service` は DB から解決した改訂に対して同じ関数を呼んで manifest の値と比較する。
    対象リポジトリのスナップショットが無くても再現できることが目的なので、`package` 単体
    （team / revision を含まない）から計算する。
    """
    canonical = json.dumps(
        package.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return content_hash(canonical)


@dataclass
class _PolicyRunResult:
    findings: list[Finding]
    levels: dict[str, RuleLevel]
    texts: list[str]
    # 規約 ID → PolicyRule（チーム規約 ∪ プロジェクト固有。同じ ID はチーム規約が優先）。
    # 例外（免除）が exceptions_allowed を尊重できるように conformance 層へ渡す
    rules_by_id: dict[str, PolicyRule]
    # チーム規約の package だけから作ったハッシュ。team_policy が無ければ None
    team_policy_hash: str | None
    # S65: Finding が出るべき規約 ID（検査器があり、対象が無いとき skipped でないもの）
    expected_rule_ids: list[str] = field(default_factory=list)


def _run_policies(
    project_dir: Path, team_policy: PolicyRevision | None, *, tolerate_errors: bool = False
) -> _PolicyRunResult:
    """チーム規約（`team_policy.package`）と `.speclino/policy/*.yaml`（プロジェクト固有の
    追加規約）の両方を VerifierRegistry で実行する。

    チーム規約を先に評価し、プロジェクト固有を後に足す（design/2026-09-26-team-policies-and-dx.md
    3章）。同じ rule id が両方にあるときはチーム規約を優先し、プロジェクト側の同 id ルールは
    実行しない（チーム規約を無効化できない、という設計）。無視した事実は `texts`
    （`policy_hash` の入力）に注記として残す。

    `tolerate_errors`（S65 の bundle の run）: ルールごとに実行し、検査器の例外はそのルールの
    UNKNOWN にして他のルールを続ける（設計 5.2）。Git の run は従来どおり例外で run を止める。
    """

    def run(package: PolicyPackage) -> list[Finding]:
        if not tolerate_errors:
            return registry.run_package(package, exceptions, cast(Snapshot, snapshot))
        out: list[Finding] = []
        for rule in package.rules:
            single = package.model_copy(update={"rules": [rule]})
            try:
                out.extend(registry.run_package(single, exceptions, cast(Snapshot, snapshot)))
            except Exception as e:  # noqa: BLE001 - 検査器の例外はそのルールの UNKNOWN
                out.append(_policy_error_finding(rule, e))
        return out

    exceptions = _load_exceptions(project_dir)
    snapshot = DirectorySnapshot(root=project_dir)
    registry = _policy_registry()

    findings: list[Finding] = []
    levels: dict[str, RuleLevel] = {}
    texts: list[str] = []
    rules_by_id: dict[str, PolicyRule] = {}
    team_policy_hash: str | None = None

    if team_policy is not None:
        team_policy_hash = policy_package_hash(team_policy.package)
        texts.append(f"team:{team_policy.team}#{team_policy.revision}:{team_policy_hash}")
        # DirectorySnapshot は frozen dataclass のため snapshot_id が読取り専用になり、
        # Snapshot Protocol（可変属性を要求）との構造的互換性チェックで mypy が誤検知する。
        team_findings = run(team_policy.package)
        findings.extend(team_findings)
        levels.update(_policy_levels(team_policy.package, team_findings))
        rules_by_id.update({rule.id: rule for rule in team_policy.package.rules})

    policy_dir = project_dir / ".speclino" / "policy"
    policy_files = (
        sorted({*policy_dir.glob("*.yaml"), *policy_dir.glob("*.yml")})
        if policy_dir.is_dir()
        else []
    )
    for path in policy_files:
        text = path.read_text(encoding="utf-8")
        package = PolicyPackage.model_validate(yaml.safe_load(text))
        overridden = sorted(rule.id for rule in package.rules if rule.id in rules_by_id)
        if overridden:
            texts.append(f"# {path.name}: チーム規約が優先するため無視: {', '.join(overridden)}")
        package = package.model_copy(
            update={"rules": [r for r in package.rules if r.id not in rules_by_id]}
        )
        texts.append(text)
        package_findings = run(package)
        findings.extend(package_findings)
        levels.update(_policy_levels(package, package_findings))
        rules_by_id.update({rule.id: rule for rule in package.rules})
    expected = [
        rule.id
        for rule in rules_by_id.values()
        if rule.verifier is not None and rule.on_missing_input != "skipped"
    ]
    return _PolicyRunResult(findings, levels, texts, rules_by_id, team_policy_hash, expected)


def _policy_error_finding(rule: PolicyRule, error: Exception) -> Finding:
    return Finding(
        id=f"policy:{rule.id}:error",
        requirement_id=rule.id,
        behavior_id=f"policy:{rule.verifier.id if rule.verifier is not None else rule.id}",
        verdict="UNKNOWN",
        category="unsupported_analysis",
        severity="high" if rule.level == "mandatory" else "medium",
        message=f"{rule.id}: 検査器の例外で判定できない（{type(error).__name__}: {error}）",
        claim_scope="persisted_state",
        next_steps=["検査器のログを確認し、規約のパラメーターと対象のファイルを見直す"],
    )


def _annotate_rejected_waivers(findings: Findings, rejected: list[RejectedWaiver]) -> Findings:
    """免除が拒否された事実を Finding.next_steps に残す（黙って無視しない）。"""
    if not rejected:
        return findings
    notes: dict[str, list[str]] = {}
    for r in rejected:
        notes.setdefault(r.finding_id, []).append(
            f"例外 {r.exception_id} は適用されない: 規約 {r.rule} は免除を許可していない"
            "（exceptions_allowed=false）。この規約自体を満たす必要がある"
        )
    updated = [
        f.model_copy(update={"next_steps": [*f.next_steps, *notes[f.id]]}) if f.id in notes else f
        for f in findings.findings
    ]
    return findings.model_copy(update={"findings": updated})


__all__ = [
    "CheckInputError",
    "CheckRequest",
    "CheckResult",
    "Mode",
    "policy_package_hash",
    "rediff",
    "run_check",
    "write_result",
]
