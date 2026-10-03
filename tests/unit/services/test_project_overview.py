"""`services/project_overview.py` の組み立て（S45）。DB も blob も使わない。"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid
from pathlib import Path

import pytest

from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.domain.finding import Finding, Verdict
from speclino.domain.policy import PolicyApplicability, PolicyPackage, PolicyRule
from speclino.domain.semantic_diff import RuleDelta, SemanticDiff
from speclino.domain.spec_ir import SpecIR
from speclino.services.project_overview import (
    ArtifactsIncomplete,
    build_policies,
    build_requirements,
    count_verdicts,
    load_spec_irs,
    requirement_verdicts,
    worst_verdict,
)

ROOT = Path(__file__).resolve().parents[3]


def _spec() -> SpecIR:
    path = ROOT / "examples/orders/specs/ir/cancel_order.spec-ir.json"
    return SpecIR.model_validate(json.loads(path.read_text()))


def _finding(
    req: str,
    verdict: Verdict,
    *,
    behavior: str = "cancel_order",
    category: str = "conformant",
    fid: str | None = None,
) -> Finding:
    return Finding.model_validate(
        {
            "id": fid or f"{req}:{verdict}:{category}",
            "requirement_id": req,
            "behavior_id": behavior,
            "verdict": verdict,
            "category": category,
            "severity": "high",
            "message": "m",
            "claim_scope": "persisted_state",
        }
    )


def test_worst_verdict_orders_violated_review_unknown_verified() -> None:
    assert worst_verdict(["VERIFIED_IN_SCOPE", "UNKNOWN", "REVIEW"]) == "REVIEW"
    assert worst_verdict(["UNKNOWN", "VIOLATED"]) == "VIOLATED"
    assert worst_verdict([]) is None


def test_requirement_verdicts_ignore_policy_findings() -> None:
    findings = [
        _finding("R-1", "VERIFIED_IN_SCOPE"),
        _finding("R-1", "UNKNOWN", category="unsupported_analysis"),
        _finding("MAKE-001:Makefile", "VIOLATED", behavior="policy:make_targets"),
    ]
    assert requirement_verdicts(findings) == {"R-1": "UNKNOWN"}


def test_count_verdicts_fills_all_four() -> None:
    c = count_verdicts(["VIOLATED", "VIOLATED", "REVIEW"])
    assert (c.VIOLATED, c.REVIEW, c.UNKNOWN, c.VERIFIED_IN_SCOPE) == (2, 1, 0, 0)


def test_requirements_follow_spec_ir_and_findings() -> None:
    spec = _spec()
    rows = build_requirements(
        [spec], [_finding("R-CANCEL-04", "VIOLATED", category="missing_effect")], None
    )
    by_id = {r.id: r for r in rows}
    assert [r.id for r in rows] == [r.id for r in spec.rules]
    assert by_id["R-CANCEL-04"].verdict == "VIOLATED"
    assert by_id["R-CANCEL-04"].approval == "approved"
    assert by_id["R-CANCEL-04"].tests_declared == 2
    assert by_id["R-CANCEL-04"].finding_ids == ["R-CANCEL-04:VIOLATED:missing_effect"]
    assert by_id["R-CANCEL-01"].verdict is None  # finding が無い = 未解析


def test_unapproved_and_stale_are_told_apart_by_semantic_diff() -> None:
    spec = _spec()
    rules = [
        r.model_copy(update={"approval_ref": None}) if r.id in {"R-CANCEL-01", "R-CANCEL-02"} else r
        for r in spec.rules
    ]
    spec = spec.model_copy(update={"rules": rules})
    diff = SemanticDiff(
        head_commit="h",
        base_available=True,
        rules=[RuleDelta(rule_id="R-CANCEL-02", behavior_id=spec.behavior_id, status="changed")],
    )
    by_id = {r.id: r for r in build_requirements([spec], [], diff)}
    assert by_id["R-CANCEL-01"].approval == "unapproved"
    assert by_id["R-CANCEL-02"].approval == "stale"
    assert by_id["R-CANCEL-03"].approval == "approved"


def test_approval_ref_missing_from_records_is_not_approved() -> None:
    spec = _spec()
    rules = [
        r.model_copy(update={"approval_ref": "nowhere"}) if r.id == "R-CANCEL-01" else r
        for r in spec.rules
    ]
    rows = build_requirements([spec.model_copy(update={"rules": rules})], [], None)
    assert {r.id: r.approval for r in rows}["R-CANCEL-01"] == "unapproved"


def test_removed_requirement_with_stale_approval_is_listed() -> None:
    spec = _spec()
    spec = spec.model_copy(update={"rules": [r for r in spec.rules if r.id != "R-CANCEL-04"]})
    rows = build_requirements(
        [spec], [_finding("R-CANCEL-04", "REVIEW", category="stale_approval")], None
    )
    removed = [r for r in rows if r.removed]
    assert [(r.id, r.approval, r.verdict) for r in removed] == [("R-CANCEL-04", "stale", "REVIEW")]


def _team() -> PolicyPackage:
    def rule(rid: str, level: str, verifier: bool) -> PolicyRule:
        return PolicyRule.model_validate(
            {
                "id": rid,
                "version": "1",
                "level": level,
                "title": f"{rid} の題",
                "text": "t",
                "applies_to": PolicyApplicability(paths=["specs/*.md"]).model_dump(),
                "verifier": {"id": "spec_format", "version": "1"} if verifier else None,
                "on_missing_input": "unknown",
            }
        )

    return PolicyPackage(
        id="team",
        version="1",
        status="active",
        owner="o",
        rules=[rule("SPEC-001", "mandatory", True), rule("DOC-001", "advisory", False)],
    )


def test_policies_combine_team_rules_and_project_findings() -> None:
    findings = [
        _finding(
            "SPEC-001:specs/a.md", "VERIFIED_IN_SCOPE", behavior="policy:spec_format", fid="p1"
        ),
        _finding("SPEC-001:specs/b.md", "VIOLATED", behavior="policy:spec_format", fid="p2"),
        _finding("MAKE-001:Makefile", "VIOLATED", behavior="policy:make_targets", fid="p3"),
        _finding("LOCAL-9:x", "VERIFIED_IN_SCOPE", behavior="policy:x", fid="p4"),
        _finding("R-1", "VIOLATED"),
    ]
    gate = {"status": "fail", "blocking_ids": ["p2", "p3"], "advisory_ids": []}
    rows = {p.rule_id: p for p in build_policies(findings, gate, _team())}
    assert list(rows) == ["SPEC-001", "DOC-001", "LOCAL-9", "MAKE-001"]
    assert (rows["SPEC-001"].origin, rows["SPEC-001"].level, rows["SPEC-001"].has_verifier) == (
        "team",
        "mandatory",
        True,
    )
    assert rows["SPEC-001"].status == "VIOLATED" and rows["SPEC-001"].counts.VERIFIED_IN_SCOPE == 1
    assert rows["DOC-001"].status is None and rows["DOC-001"].has_verifier is False
    assert (rows["MAKE-001"].origin, rows["MAKE-001"].level, rows["MAKE-001"].title) == (
        "project",
        "mandatory",
        None,
    )
    assert rows["LOCAL-9"].level is None and rows["LOCAL-9"].has_verifier is None


def test_policies_without_team_or_gate() -> None:
    assert build_policies([_finding("R-1", "VIOLATED")], None, None) == []


def _put(store: FileSystemBlobStore, run_id: _uuid.UUID, name: str, spec: SpecIR) -> None:
    asyncio.run(store.put(f"runs/{run_id}/{name}", spec.model_dump_json().encode()))


def test_missing_behavior_spec_ir_is_incomplete(tmp_path: Path) -> None:
    store, run_id, spec = FileSystemBlobStore(tmp_path), _uuid.uuid4(), _spec()
    other = spec.model_copy(update={"behavior_id": "refund"})
    _put(store, run_id, "spec-ir.json", spec)  # 複数 behavior でも先頭の spec-ir.json は残る
    _put(store, run_id, f"spec-ir.{spec.behavior_id}.json", spec)
    with pytest.raises(FileNotFoundError):  # refund のファイルが無い
        asyncio.run(load_spec_irs(store, run_id, [spec.behavior_id, "refund"]))
    _put(store, run_id, "spec-ir.refund.json", other)
    assert [
        s.behavior_id
        for s in asyncio.run(load_spec_irs(store, run_id, [spec.behavior_id, "refund"]))
    ] == [spec.behavior_id, "refund"]


def test_single_behavior_must_match_manifest(tmp_path: Path) -> None:
    store, run_id, spec = FileSystemBlobStore(tmp_path), _uuid.uuid4(), _spec()
    _put(store, run_id, "spec-ir.json", spec)
    with pytest.raises(ArtifactsIncomplete):
        asyncio.run(load_spec_irs(store, run_id, ["refund"]))


def test_policies_count_findings_waived_by_exceptions() -> None:
    """例外で免除された規約の違反を数える（ゲートが「例外付き通過」になる理由を画面で示す）。"""
    findings = [
        _finding("MAKE-001:Makefile", "VIOLATED", behavior="policy:make_targets", fid="w1"),
        _finding("MAKE-001:sub/Makefile", "VIOLATED", behavior="policy:make_targets", fid="w2"),
        _finding(
            "MAKE-001:x/Makefile", "VERIFIED_IN_SCOPE", behavior="policy:make_targets", fid="ok"
        ),
    ]
    gate = {
        "status": "pass_with_exceptions",
        "blocking_ids": [],
        "advisory_ids": [],
        "waived_ids": ["w1"],
    }
    [row] = build_policies(findings, gate, None)
    assert (row.status, row.waived) == ("VIOLATED", 1)
    [no_gate] = build_policies(findings, None, None)
    assert no_gate.waived == 0
