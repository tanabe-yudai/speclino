"""期待する Finding の列挙と欠落の補完（設計 5.2）。"""

from __future__ import annotations

from speclino.conformance.expected import expected_ids, fill_missing, obligation_levels
from speclino.conformance.gate import aggregate_gate
from speclino.domain import Findings
from speclino.domain.finding import Finding, VerdictCounts
from speclino.domain.uml import (
    Automaton,
    CompiledSpec,
    StateMachine,
    StructureObligation,
    TraceObligation,
)
from tests.fixtures import cancel_order as co

H = "sha256:" + "0" * 64


def _compiled() -> CompiledSpec:
    return CompiledSpec(
        parser_version="test",
        source_hash=H,
        ir_hash=H,
        structure=[StructureObligation(id="cls#Order", class_name="Order", level="advisory")],
        traces=[
            TraceObligation(
                id="seq#Svc.place",
                diagram_kind="sequence",
                entry_operation="Svc.place",
                mode="contains_subsequence",
                automaton=Automaton(states=["s0"], initial="s0", accepting=["s0"], transitions=[]),
                level="mandatory",
            )
        ],
        state_machines=[
            StateMachine(
                id="st#order.status",
                attribute="order.status",
                states=["A"],
                initial="A",
                transitions=[],
                level="mandatory",
            )
        ],
        legacy=[co.SPEC],
    )


def test_expected_ids_lists_obligations_legacy_and_policies() -> None:
    ids = expected_ids(_compiled(), ["P-NAMING", "seq#Svc.place"])
    assert ids[:3] == ["cls#Order", "seq#Svc.place", "st#order.status"]
    assert [r.id for r in co.SPEC.rules] == ids[3 : 3 + len(co.SPEC.rules)]
    assert ids[-1] == "P-NAMING"
    assert len(ids) == len(set(ids))


def test_obligation_levels() -> None:
    levels = obligation_levels(_compiled())
    assert levels["cls#Order"] == "advisory"
    assert levels["seq#Svc.place"] == "mandatory"


def _verified(rid: str) -> Finding:
    return Finding(
        id=f"F-{rid}",
        requirement_id=rid,
        behavior_id="b",
        verdict="VERIFIED_IN_SCOPE",
        category="conformant",
        severity="info",
        message="成立",
        claim_scope="command_invocation",
    )


def test_missing_expected_is_unknown() -> None:
    fs = Findings(findings=[_verified("seq#Svc.place")], counts=VerdictCounts(VERIFIED_IN_SCOPE=1))
    out = fill_missing(fs, ["seq#Svc.place", "st#order.status", "st#order.status"], "b")
    added = [f for f in out.findings if f.requirement_id == "st#order.status"]
    assert len(added) == 1
    assert (added[0].verdict, added[0].category) == ("UNKNOWN", "missing_finding")
    assert out.counts == VerdictCounts(VERIFIED_IN_SCOPE=1, UNKNOWN=1)
    # 決定的
    assert fill_missing(fs, ["st#order.status"], "b").findings[-1].id == added[0].id
    # 必須の要求の欠落はゲートを落とす（フェイルクローズ）
    gate = aggregate_gate(out, {"st#order.status": "mandatory", "seq#Svc.place": "mandatory"})
    assert gate.status == "fail"


def test_nothing_missing_returns_same() -> None:
    fs = Findings(findings=[_verified("x")], counts=VerdictCounts(VERIFIED_IN_SCOPE=1))
    assert fill_missing(fs, ["x"], "b") == fs


def _finding(rid: str, verdict: str) -> Finding:
    return Finding(
        id=f"f-{rid}",
        requirement_id=rid,
        behavior_id="b",
        verdict=verdict,  # type: ignore[arg-type]
        category="missing_finding",
        severity="low",
        message="m",
        claim_scope="command_invocation",
    )


def test_uml_coverage_counts_requirement_rows() -> None:
    from speclino.conformance.expected import uml_coverage
    from speclino.domain.uml import RequirementRow

    compiled = _compiled().model_copy(
        update={
            "requirements": [
                RequirementRow(id="R1", level="mandatory", text="a", formalized_by=["cls#x"]),
                RequirementRow(id="R2", level="mandatory", text="b", formalized_by=["seq#y"]),
                RequirementRow(id="R3", level="advisory", text="c", formalized_by=[]),
                RequirementRow(id="R4", level="mandatory", text="d", formalized_by=["ir:X"]),
            ]
        }
    )
    findings = [_finding("cls#Order", "VERIFIED_IN_SCOPE"), _finding("seq#Svc.place", "UNKNOWN")]
    c = uml_coverage(compiled, findings, approved=True)
    # R4 は旧形式の要求（engine が数える）なので除く
    assert (c.requirements_total, c.requirements_approved) == (3, 3)
    assert (c.requirements_in_scope, c.requirements_unanalyzed) == (2, 1)
    assert c.requirements_decided == 1
    assert uml_coverage(compiled, findings, approved=False).requirements_approved == 0
