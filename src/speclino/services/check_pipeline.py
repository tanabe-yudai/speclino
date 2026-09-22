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
    (c) `code_ir_override` があればそれを読む（behavior_id 一致を確認）、無ければ
        `analyze_repo` で Code IR を作る
    (d) `conformance.engine.evaluate` で Findings を作る（複数 behavior は結合する）
    (e) `.speclino/policy/*.yaml` があれば VerifierRegistry で実行し Findings に追加する
    (f) `conformance.gate.aggregate_gate` でゲート判定する
    (g) `RunManifest` を組み立てる（入力ハッシュ・版・上限・fail_on・mode）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import yaml
import z3

from speclino import __version__
from speclino.analysis.limits import DEFAULT_LIMITS, clamp
from speclino.analysis.path_analyzer import ANALYZER_VERSION, analyze_repo
from speclino.conformance.engine import ENGINE_VERSION, EngineConfig, evaluate
from speclino.conformance.gate import DEFAULT_FAIL_ON, GatePolicy, GateResult, aggregate_gate
from speclino.domain.bindings import Bindings, OperationContract
from speclino.domain.code_ir import AnalysisLimits, CodeIR
from speclino.domain.common import SnapshotRef
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.finding import CoverageCounts, Finding, Findings, Verdict, VerdictCounts
from speclino.domain.policy import PolicyExceptions, PolicyPackage
from speclino.domain.run import RunInputs, RunManifest, RunVersions
from speclino.domain.spec_ir import RuleLevel, SpecIR
from speclino.reporter.json_out import write_artifacts
from speclino.spec import (
    ValidationReport,
    content_hash,
    load_bindings,
    load_contracts,
    load_domain,
    load_spec_ir,
    validate_spec_ir,
)
from speclino.verifiers.base import Snapshot
from speclino.verifiers.exceptions import load_policy_exceptions
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_format import SpecFormatVerifier

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
    artifacts: dict[str, Path] | None = None
    domain: DomainSpec | None = None
    bindings: Bindings | None = None
    contracts: dict[str, OperationContract] = field(default_factory=dict)


def run_check(req: CheckRequest) -> CheckResult:
    project_dir = req.resolved_project_dir
    limits = clamp(req.limits)

    domain_path = project_dir / ".speclino" / "domain.yaml"
    bindings_path = project_dir / ".speclino" / "bindings.yaml"
    if not domain_path.is_file():
        raise CheckInputError(f"domain.yaml が無い: {domain_path}")
    if not bindings_path.is_file():
        raise CheckInputError(f"bindings.yaml が無い: {bindings_path}")
    domain = load_domain(domain_path)
    bindings = load_bindings(bindings_path)

    contracts_dir = project_dir / ".speclino" / "contracts"
    contracts = load_contracts(contracts_dir) if contracts_dir.is_dir() else {}

    spec_ir_dir = project_dir / "specs" / "ir"
    spec_ir_files = sorted(spec_ir_dir.glob("*.spec-ir.json")) if spec_ir_dir.is_dir() else []
    if not spec_ir_files:
        raise CheckInputError(f"Spec IR が無い: {spec_ir_dir}/*.spec-ir.json")

    spec_irs: list[SpecIR] = []
    validations: list[ValidationReport] = []
    spec_markdown_texts: list[str] = []
    spec_ir_texts: list[str] = []
    for path in spec_ir_files:
        raw_text = path.read_text(encoding="utf-8")
        spec_ir_texts.append(raw_text)
        spec = load_spec_ir(path)
        if req.spec_overrides and spec.behavior_id in req.spec_overrides:
            spec = req.spec_overrides[spec.behavior_id]
        spec_irs.append(spec)
        markdown_text = _find_spec_markdown(project_dir, spec)
        if markdown_text is not None:
            spec_markdown_texts.append(markdown_text)
        validations.append(validate_spec_ir(spec, domain, bindings, markdown_text))

    error_messages = [
        f"{report.spec.behavior_id}: [{d.code}] {d.rule_id or '-'} {d.path or ''} {d.message}"
        for report in validations
        for d in report.errors
    ]
    if error_messages:
        raise CheckInputError("Spec IR の検証エラー:\n" + "\n".join(error_messages))

    snapshot = SnapshotRef(
        snapshot_id=req.head_sha,
        repository=str(req.repo_root),
        commit=None if req.head_sha == "WORKTREE" else req.head_sha,
    )

    override, override_text = _load_override(req.code_ir_override, spec_irs)

    code_irs: list[CodeIR] = []
    for spec in spec_irs:
        if override is not None and override.behavior_id == spec.behavior_id:
            code_irs.append(override)
            continue
        code_irs.append(
            analyze_repo(
                req.repo_root,
                spec.behavior_id,
                domain,
                bindings,
                contracts,
                limits,
                snapshot,
                req.source_roots,
            )
        )

    run_id = req.run_id or str(uuid4())
    engine_config = EngineConfig(solver_timeout_ms=limits.solver_timeout_ms)
    findings_parts = [
        evaluate(spec, code, domain, engine_config, run_id)
        for spec, code in zip(spec_irs, code_irs, strict=True)
    ]
    combined = _merge_findings(findings_parts, run_id)

    policy_findings, policy_levels, policy_texts = _run_policies(project_dir)
    combined = _with_extra_findings(combined, [*policy_findings, *req.extra_findings])

    levels: dict[str, RuleLevel] = {rule.id: rule.level for spec in spec_irs for rule in spec.rules}
    levels.update(policy_levels)

    fail_on = req.fail_on or DEFAULT_FAIL_ON
    gate = aggregate_gate(combined, levels, GatePolicy(fail_on=fail_on, mode=req.mode))

    manifest = RunManifest(
        run_id=run_id,
        created_at=datetime.now(UTC),
        inputs=RunInputs(
            repository=str(req.repo_root),
            head_commit=req.head_sha,
            base_commit=req.base_sha,
            spec_content_hash=_combined_hash(spec_markdown_texts),
            spec_ir_hash=_combined_hash(spec_ir_texts),
            bindings_hash=content_hash(bindings_path.read_text(encoding="utf-8")),
            domain_hash=content_hash(domain_path.read_text(encoding="utf-8")),
            contracts_hash=_combined_hash(_contract_texts(contracts_dir)),
            policy_hash=_combined_hash(policy_texts),
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
    )

    return CheckResult(
        manifest=manifest,
        spec_irs=spec_irs,
        code_irs=code_irs,
        findings=combined,
        gate=gate,
        validation=validations,
        policy_findings=policy_findings,
        domain=domain,
        bindings=bindings,
        contracts=contracts,
    )


def write_result(result: CheckResult, out_dir: Path) -> dict[str, Path]:
    """成果物一式を `out_dir` に書く（`reporter.write_artifacts` の薄いラッパー）。

    behavior が1件なら `write_artifacts` の出力（spec-ir.json / code-ir.json）をそのまま使う。
    複数 behavior のときは、結合済み Findings で run-manifest.json / findings.json / gate.json /
    review.md を1組だけ書き、spec-ir / code-ir は behavior ごとに
    `spec-ir.<behavior_id>.json` / `code-ir.<behavior_id>.json` を追加で書く。
    """
    if len(result.spec_irs) == 1:
        code = result.code_irs[0] if result.code_irs else None
        return write_artifacts(
            out_dir, result.manifest, result.spec_irs[0], code, result.findings, result.gate
        )

    written = write_artifacts(
        out_dir, result.manifest, result.spec_irs[0], None, result.findings, result.gate
    )
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


def _run_policies(project_dir: Path) -> tuple[list[Finding], dict[str, RuleLevel], list[str]]:
    """`.speclino/policy/*.yaml` があれば VerifierRegistry で実行する。無ければ何もしない。"""
    policy_dir = project_dir / ".speclino" / "policy"
    if not policy_dir.is_dir():
        return [], {}, []

    exceptions_path = project_dir / ".speclino" / "exceptions.yaml"
    exceptions = (
        load_policy_exceptions(yaml.safe_load(exceptions_path.read_text(encoding="utf-8")) or {})
        if exceptions_path.is_file()
        else PolicyExceptions(exceptions=[])
    )
    snapshot = DirectorySnapshot(root=project_dir)
    registry = _policy_registry()

    findings: list[Finding] = []
    levels: dict[str, RuleLevel] = {}
    texts: list[str] = []
    policy_files = sorted({*policy_dir.glob("*.yaml"), *policy_dir.glob("*.yml")})
    for path in policy_files:
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        package = PolicyPackage.model_validate(yaml.safe_load(text))
        # DirectorySnapshot は frozen dataclass のため snapshot_id が読取り専用になり、
        # Snapshot Protocol（可変属性を要求）との構造的互換性チェックで mypy が誤検知する。
        package_findings = registry.run_package(package, exceptions, cast(Snapshot, snapshot))
        findings.extend(package_findings)
        levels.update(_policy_levels(package, package_findings))
    return findings, levels, texts


__all__ = [
    "CheckInputError",
    "CheckRequest",
    "CheckResult",
    "Mode",
    "run_check",
    "write_result",
]
