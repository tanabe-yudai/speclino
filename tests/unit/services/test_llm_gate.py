"""LLM 支援の Finding と人の確定のゲートでの扱い（S70、設計 6.2・8 章）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from speclino.conformance.gate import aggregate_gate
from speclino.domain.assessment import Attestation
from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import (
    PolicyApplicability,
    PolicyPackage,
    PolicyRevision,
    PolicyRule,
    PolicyVerifierRef,
)
from speclino.services.attestations import attestation_target
from speclino.services.check_pipeline import (
    CheckResult,
    llm_assisted_rules,
    run_check,
    write_result,
)
from speclino.services.llm_policies import (
    NO_ISSUE_BLOCKING_LABEL,
    InMemoryAssessmentStore,
    assess,
    checklist_params,
)
from speclino.services.llm_units import extract_units
from tests.unit.services.test_check_pipeline_bundle import (
    UML,
    by_req,
    knowledge,
    make_repo,
    request,
    spec_revision,
)
from tests.unit.services.test_llm_policies import FakeLlm, _answer

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
RULE_ID = "FN-INTENT"


def _llm_policy(level: str = "mandatory") -> PolicyRevision:
    rule = PolicyRule(
        id=RULE_ID,
        version="1",
        level=level,  # type: ignore[arg-type]
        title="名前が意図を表す",
        text="関数名が処理の意図を表すこと",
        applies_to=PolicyApplicability(paths=["examples/orders/app/inventory.py"]),
        verifier=PolicyVerifierRef(
            id="llm.checklist",
            version="1",
            params={
                "items": [{"id": "intent", "question": "名前が意図を表すか"}],
                "unit_kind": "function",
            },
        ),
        on_missing_input="unknown",
        tier="llm_assisted",
    )
    package = PolicyPackage(id="team", version="1", status="active", owner="w", rules=[rule])
    return PolicyRevision(team="w", revision=4, status="active", package=package, author="a")


async def _llm_findings(
    root: Path, policy: PolicyRevision, answer: str, store: InMemoryAssessmentStore | None = None
) -> tuple[dict[str, list[Finding]], list[str]]:
    rule = policy.package.rules[0]
    units = extract_units(root, checklist_params(rule), rule.applies_to.paths)
    found, keys = await assess(
        rule,
        units.units,
        llm=FakeLlm([answer]),
        store=store or InMemoryAssessmentStore(),
        policy_revision=policy.revision,
        unassessable=units.problems,
        origin=f"team:w#{policy.revision}",
    )
    return {rule.id: found}, keys


def _llm(res: CheckResult) -> list[Finding]:
    return [f for f in res.findings.findings if f.requirement_id.startswith(RULE_ID)]


def _ids(fs: list[Finding]) -> set[str]:
    return {f.id for f in fs}


def _attest(f: Finding, disposition: str = "attested", **over: Any) -> Attestation:
    target = attestation_target(f)
    assert target is not None
    data: dict[str, Any] = {
        "fingerprint": target[0],
        "content_hash": target[1],
        "disposition": disposition,
        "actor": "u1",
        "workspace": "w",
        "created_at": NOW,
    }
    data.update(over)
    return Attestation.model_validate(data)


async def test_no_issue_blocks_mandatory_until_attested(tmp_path: Path) -> None:
    """#114 のユーザー判断: 必須の LLM 支援の規約は、指摘なしでも人の確定まで止める。"""
    root = make_repo(tmp_path / "r")
    policy = _llm_policy()
    k = knowledge(spec_revision("u", UML), policy=policy)
    llm, keys = await _llm_findings(root, policy, _answer(False, "", item="intent"))
    res = run_check(request(root, k, llm_findings=llm, llm_assessment_keys=tuple(keys)))
    found = _llm(res)
    assert found and all((f.verdict, f.assessment_outcome) == ("REVIEW", "no_issue") for f in found)
    assert _ids(found) <= _ids(res.gate.blocking)
    assert not _ids(found) & _ids(res.gate.cleared)
    assert res.gate.status == "fail"
    assert all(f.message.endswith(NO_ISSUE_BLOCKING_LABEL) for f in found)
    assert all(attestation_target(f) is not None for f in found)
    assert res.manifest.llm_assessment_keys == sorted(keys) and keys

    # attested の確定だけが阻止から外す（acknowledged_violation は阻止したまま）
    oks = tuple(_attest(f) for f in found)
    attested = run_check(request(root, k, llm_findings=llm, attestations=oks))
    assert _ids(found) <= _ids(attested.gate.cleared)
    assert not _ids(found) & _ids(attested.gate.blocking)
    acks = tuple(_attest(f, "acknowledged_violation") for f in found)
    acked = run_check(request(root, k, llm_findings=llm, attestations=acks))
    assert _ids(found) <= _ids(acked.gate.blocking)

    out = tmp_path / "out"
    write_result(res, out)
    assert "cleared_ids" not in json.loads((out / "gate.json").read_text())


async def test_no_issue_of_advisory_rule_does_not_block(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    policy = _llm_policy("advisory")
    llm, _ = await _llm_findings(root, policy, _answer(False, "", item="intent"))
    res = run_check(
        request(root, knowledge(spec_revision("u", UML), policy=policy), llm_findings=llm)
    )
    found = _llm(res)
    assert found and _ids(found) <= _ids(res.gate.advisory)
    assert not _ids(found) & _ids(res.gate.blocking)
    assert all(not f.message.endswith(NO_ISSUE_BLOCKING_LABEL) for f in found)


async def test_issue_blocks_when_mandatory_and_is_advisory_otherwise(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    for level, bucket in (("mandatory", "blocking"), ("advisory", "advisory")):
        policy = _llm_policy(level)
        llm, _ = await _llm_findings(root, policy, _answer(True, "def restore", item="intent"))
        res = run_check(
            request(root, knowledge(spec_revision("u", UML), policy=policy), llm_findings=llm)
        )
        found = _llm(res)
        assert found and all(f.assessment_outcome == "issue" for f in found)
        assert _ids(found) <= _ids(getattr(res.gate, bucket))
        assert res.gate.status == "fail"


async def test_attestation_bound_to_content_hash(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    policy = _llm_policy()
    k = knowledge(spec_revision("u", UML), policy=policy)
    llm, _ = await _llm_findings(root, policy, _answer(True, "def restore", item="intent"))
    first = run_check(request(root, k, llm_findings=llm))
    target = _llm(first)[0]
    ok = _attest(target)

    attested = run_check(request(root, k, llm_findings=llm, attestations=(ok,)))
    assert target.id in _ids(attested.gate.cleared)
    assert target.id not in _ids(attested.gate.blocking)
    # verdict は書き換えない。確定は manifest に残る
    assert by_req_id(attested, target.id).verdict == "REVIEW"
    assert attested.manifest.attestations == [ok]

    # hash が違う確定は効かない
    wrong = _attest(target, content_hash="sha256:" + "f" * 64)
    res = run_check(request(root, k, llm_findings=llm, attestations=(wrong,)))
    assert target.id in _ids(res.gate.blocking) and res.manifest.attestations == []

    # 問題を認めた（acknowledged_violation）だけでは阻止したまま
    ack = _attest(target, "acknowledged_violation")
    res = run_check(request(root, k, llm_findings=llm, attestations=(ack,)))
    assert target.id in _ids(res.gate.blocking) and res.manifest.attestations == [ack]

    # コードが変われば単位の hash と指紋が変わり、前の確定は効かない
    inventory = root / "examples" / "orders" / "app" / "inventory.py"
    src = inventory.read_text()
    inventory.write_text(src.replace(".append(items)", ".append(tuple(items))"))
    llm2, _ = await _llm_findings(root, policy, _answer(True, "def restore", item="intent"))
    changed = run_check(request(root, k, llm_findings=llm2, attestations=(ok,)))
    assert _ids(_llm(changed)) != _ids(_llm(first))  # 同じ場所でも指紋が変わる
    assert _ids(_llm(changed)) <= _ids(changed.gate.blocking)
    assert changed.manifest.attestations == []


def by_req_id(res: CheckResult, fid: str) -> Finding:
    return next(f for f in res.findings.findings if f.id == fid)


async def test_unassessed_llm_rule_is_unknown(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    res = run_check(request(root, knowledge(spec_revision("u", UML), policy=_llm_policy())))
    (f,) = _llm(res)
    assert (f.verdict, f.assessment_outcome) == ("UNKNOWN", None)
    assert f.id in _ids(res.gate.blocking)


async def test_llm_failure_blocks_the_gate(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    policy = _llm_policy()
    rule = policy.package.rules[0]
    units = extract_units(root, checklist_params(rule), rule.applies_to.paths)
    found, _ = await assess(
        rule,
        units.units,
        llm=FakeLlm([RuntimeError("down")]),
        store=InMemoryAssessmentStore(),
        policy_revision=4,
    )
    res = run_check(
        request(
            root, knowledge(spec_revision("u", UML), policy=policy), llm_findings={RULE_ID: found}
        )
    )
    assert _llm(res) and all(f.verdict == "UNKNOWN" for f in _llm(res))
    assert _ids(_llm(res)) <= _ids(res.gate.blocking)


def test_llm_assisted_rules_follow_policy_precedence(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    k = knowledge(spec_revision("u", UML), policy=_llm_policy())
    rules = llm_assisted_rules(request(root, k))
    assert [(t.rule.id, t.policy_revision, t.origin) for t in rules] == [(RULE_ID, 4, "team:w#4")]
    assert llm_assisted_rules(request(root, knowledge(spec_revision("u", UML)))) == []


def test_bindings_change_is_attestable_per_content(tmp_path: Path) -> None:
    default = make_repo(tmp_path / "default")
    head = make_repo(tmp_path / "head")
    bindings = head / ".speclino" / "bindings.yaml"
    bindings.write_text(bindings.read_text() + "\n# 変更\n")
    k = knowledge(spec_revision("u", UML))
    first = run_check(request(head, k, trusted_root=default))
    notice = by_req(first.findings.findings)["KNOWLEDGE-BINDINGS-001"]
    target = attestation_target(notice)
    assert target is not None and target[0] == f"KNOWLEDGE-BINDINGS-001:{target[1]}"
    ok = _attest(notice)
    cleared = run_check(request(head, k, trusted_root=default, attestations=(ok,)))
    assert notice.id in _ids(cleared.gate.cleared)
    # 別の変更は確定し直しが要る
    bindings.write_text(bindings.read_text() + "# もう一度\n")
    again = run_check(request(head, k, trusted_root=default, attestations=(ok,)))
    assert notice.id in _ids(again.gate.blocking)


def test_untrusted_notice_is_not_attestable(tmp_path: Path) -> None:
    head = make_repo(tmp_path / "head")
    res = run_check(request(head, knowledge(spec_revision("u", UML)), trusted_root=None))
    assert attestation_target(by_req(res.findings.findings)["KNOWLEDGE-BINDINGS-001"]) is None


def test_cleared_ids_only_clear_review() -> None:
    def f(fid: str, verdict: str) -> Finding:
        return Finding(
            id=fid,
            requirement_id=fid,
            behavior_id="b",
            verdict=verdict,  # type: ignore[arg-type]
            category="potential_violation",
            severity="high",
            message="m",
            claim_scope="persisted_state",
        )

    findings = Findings(findings=[f("a", "REVIEW"), f("b", "UNKNOWN"), f("c", "VIOLATED")])
    gate = aggregate_gate(findings, {}, cleared_ids=frozenset({"a", "b", "c"}))
    assert _ids(gate.cleared) == {"a"} and _ids(gate.blocking) == {"b", "c"}
    only_review = aggregate_gate(
        Findings(findings=[f("a", "REVIEW")]), {}, cleared_ids=frozenset({"a"})
    )
    assert only_review.status == "pass"


def test_project_rule_origin_follows_the_file_content(tmp_path: Path) -> None:
    """プロジェクト固有の規約は改訂番号が無い（1）ので、出所にファイルの内容の hash を入れる。"""
    import yaml

    root = make_repo(tmp_path / "r")
    policy_dir = root / ".speclino" / "policy"
    policy_dir.mkdir(exist_ok=True)
    package = _llm_policy().package.model_dump(mode="json", by_alias=True)
    (policy_dir / "llm.yaml").write_text(yaml.safe_dump(package, allow_unicode=True))
    (first,) = llm_assisted_rules(request(root, None))
    package["rules"][0]["text"] = "別の本文"
    (policy_dir / "llm.yaml").write_text(yaml.safe_dump(package, allow_unicode=True))
    (second,) = llm_assisted_rules(request(root, None))
    assert first.policy_revision == second.policy_revision == 1
    assert first.origin.startswith("project:") and first.origin != second.origin


async def test_self_attestation_is_marked_in_gate_and_manifest(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    policy = _llm_policy()
    k = knowledge(spec_revision("u", UML), policy=policy)
    llm, _ = await _llm_findings(root, policy, _answer(True, "def restore", item="intent"))
    target = _llm(run_check(request(root, k, llm_findings=llm)))[0]
    own = _attest(target, self_attested=True)
    res = run_check(request(root, k, llm_findings=llm, attestations=(own,)))
    assert target.id in _ids(res.gate.cleared)  # 拒否はしない
    assert res.gate.self_attested_ids == [target.id]
    assert [a.self_attested for a in res.manifest.attestations] == [True]
    out = tmp_path / "out"
    write_result(res, out)
    gate = json.loads((out / "gate.json").read_text())
    assert gate["self_attested_ids"] == [target.id]
    other = run_check(
        request(root, k, llm_findings=llm, attestations=(_attest(target, self_attested=False),))
    )
    assert other.gate.self_attested_ids == [] and other.gate.self_attest_unverified_ids == []
    # 照合できない確定は「作成者との照合不可」として別に残す（黙って自己確定でない扱いにしない）
    unknown = run_check(
        request(root, k, llm_findings=llm, attestations=(_attest(target, self_attested=None),))
    )
    assert unknown.gate.self_attest_unverified_ids == [target.id]
    assert [a.self_attested for a in unknown.manifest.attestations] == [None]
    write_result(unknown, tmp_path / "out2")
    gate2 = json.loads((tmp_path / "out2" / "gate.json").read_text())
    assert gate2["self_attest_unverified_ids"] == [target.id]
    assert "self_attested_ids" not in gate2


def test_self_attest_helpers() -> None:
    from speclino.services.attestations import (
        config_change_warning,
        same_github_user,
        self_attest_label,
    )

    assert same_github_user(1, 1) is True
    assert same_github_user(1, 2) is False
    assert same_github_user(None, 1) is None and same_github_user(1, None) is None
    assert self_attest_label(None) == "作成者との照合不可"
    cfg = "KNOWLEDGE-BINDINGS-001:sha256:" + "a" * 64
    assert config_change_warning(cfg, True) and config_change_warning(cfg, None)
    assert config_change_warning(cfg, False) is None
    assert config_change_warning("R:i:1", True) is None


async def test_regate_recounts_self_attest_marks_from_current_findings(tmp_path: Path) -> None:
    """エージェントで Findings が変わった後の再ゲートは、今の Findings から印を数え直す。"""
    from speclino.services.review_run_service import _regate

    root = make_repo(tmp_path / "r")
    policy = _llm_policy()
    k = knowledge(spec_revision("u", UML), policy=policy)
    llm, _ = await _llm_findings(root, policy, _answer(True, "def restore", item="intent"))
    target = _llm(run_check(request(root, k, llm_findings=llm)))[0]
    res = run_check(
        request(root, k, llm_findings=llm, attestations=(_attest(target, self_attested=True),))
    )
    same = _regate(res.findings, res, ("VIOLATED", "UNKNOWN", "REVIEW"))
    assert same.self_attested_ids == [target.id]
    without = res.findings.model_copy(
        update={"findings": [f for f in res.findings.findings if f.id != target.id]}
    )
    assert _regate(without, res, ("VIOLATED", "UNKNOWN", "REVIEW")).self_attested_ids == []
