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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
from speclino.conformance.engine import ENGINE_VERSION, EngineConfig, EngineInputError, evaluate
from speclino.conformance.exceptions import waived_ids
from speclino.conformance.gate import DEFAULT_FAIL_ON, GatePolicy, GateResult, aggregate_gate
from speclino.conformance.semantic_diff import DiffSide, annotate_findings, semantic_diff
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
from speclino.domain.policy import PolicyExceptions, PolicyPackage
from speclino.domain.run import BehaviorAnalysis, RunInputs, RunManifest, RunVersions
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import RuleLevel, SpecIR
from speclino.reporter.json_out import write_artifacts
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
from speclino.verifiers.api_error_format import ApiErrorFormatVerifier
from speclino.verifiers.base import Snapshot
from speclino.verifiers.ci_required import CiRequiredVerifier
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
    # 例外の期限判定に使う日付（テストで固定できる）
    today: date | None = None
    # 人の回答（human_ask）による run 限定の操作束縛: 操作 ID → 実装シンボル（S32）
    bindings_patch: dict[str, str] | None = None
    # S28: base スナップショット。指定があれば「base 仕様 + base コード」でも判定し、head の
    # Findings と突き合わせて意味差分（新規 / 既存 / 解消 / 判定不能化）を作る
    base_root: Path | None = None
    base_project_dir: Path | None = None

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
    semantic_diff: SemanticDiff | None = None
    # 意味差分の base 側（エージェントの再解析後に差分を取り直すために保持する）
    base_side: DiffSide | None = None
    base_error: str | None = None


def run_check(req: CheckRequest) -> CheckResult:
    project_dir = req.resolved_project_dir
    limits = clamp(req.limits)

    domain_path = project_dir / ".speclino" / "domain.yaml"
    bindings_path = project_dir / ".speclino" / "bindings.yaml"
    if not domain_path.is_file():
        raise CheckInputError(f"domain.yaml が無い: {domain_path}")
    if not bindings_path.is_file():
        raise CheckInputError(f"bindings.yaml が無い: {bindings_path}")
    domain_text = domain_path.read_text(encoding="utf-8")
    bindings_text = bindings_path.read_text(encoding="utf-8")
    domain = load_domain(domain_path)
    bindings = load_bindings(bindings_path)
    if req.bindings_patch:
        ops = {
            op_id: (
                ob.model_copy(update={"symbol": req.bindings_patch[op_id]})
                if op_id in req.bindings_patch
                else ob
            )
            for op_id, ob in bindings.operations.items()
        }
        bindings = bindings.model_copy(update={"operations": ops})

    contracts_dir = project_dir / ".speclino" / "contracts"
    contracts = load_contracts(contracts_dir) if contracts_dir.is_dir() else {}
    # S41c: 増分解析の入力同一性（inputs_hash）と RunInputs の両方でこの3つを使い回す
    domain_hash = content_hash(domain_text)
    bindings_hash = content_hash(bindings_text)
    contracts_hash = _combined_hash(_contract_texts(contracts_dir))

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
    for spec in spec_irs:
        # 到達関数群のハッシュは override 時も含め常に計算する（再利用の判定材料にしないときも
        # manifest に残し、「解析していない」ことを隠さない）
        digest = reachable_source_hash(req.repo_root, spec.behavior_id, bindings, req.source_roots)
        inputs_hash = _inputs_hash(
            rules_content_hash(spec.rules), domain_hash, bindings_hash, contracts_hash
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
                domain,
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
        evaluate(spec, code, domain, engine_config, run_id)
        for spec, code in zip(spec_irs, code_irs, strict=True)
    ]
    combined = _merge_findings(findings_parts, run_id)

    policy_findings, policy_levels, policy_texts = _run_policies(project_dir)
    combined = _with_extra_findings(combined, [*policy_findings, *req.extra_findings])

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

    levels: dict[str, RuleLevel] = {rule.id: rule.level for spec in spec_irs for rule in spec.rules}
    levels.update(policy_levels)

    fail_on = req.fail_on or DEFAULT_FAIL_ON
    # 例外（免除）: 有効期限内で対象が一致する VIOLATED だけ。適合には書き換えず別記する（AC-009）
    exceptions = _load_exceptions(project_dir)
    waived = waived_ids(combined, exceptions, req.today or date.today())
    gate = aggregate_gate(combined, levels, GatePolicy(fail_on=fail_on, mode=req.mode), waived)

    manifest = RunManifest(
        run_id=run_id,
        created_at=datetime.now(UTC),
        inputs=RunInputs(
            repository=str(req.repo_root),
            head_commit=req.head_sha,
            base_commit=req.base_sha,
            spec_content_hash=_combined_hash(spec_markdown_texts),
            spec_ir_hash=_combined_hash(spec_ir_texts),
            bindings_hash=bindings_hash,
            domain_hash=domain_hash,
            contracts_hash=contracts_hash,
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
        analysis=analysis_records,
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
        semantic_diff=diff,
        base_side=base_side,
        base_error=base_error,
    )


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
        )
        _write_domain(result, out_dir, written)
        return written

    written = write_artifacts(
        out_dir,
        result.manifest,
        result.spec_irs[0],
        None,
        result.findings,
        result.gate,
        result.semantic_diff,
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
    )
    try:
        return run_check(base_req), None
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
    registry.register(CiRequiredVerifier())
    registry.register(ApiErrorFormatVerifier())
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


def _run_policies(project_dir: Path) -> tuple[list[Finding], dict[str, RuleLevel], list[str]]:
    """`.speclino/policy/*.yaml` があれば VerifierRegistry で実行する。無ければ何もしない。"""
    policy_dir = project_dir / ".speclino" / "policy"
    if not policy_dir.is_dir():
        return [], {}, []

    exceptions = _load_exceptions(project_dir)
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
    "rediff",
    "run_check",
    "write_result",
]
