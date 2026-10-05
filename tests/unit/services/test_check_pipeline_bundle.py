"""S65: bundle を固定した run（services/check_pipeline.py の knowledge 経路）。DB は使わない。

`ResolvedKnowledge` を直接組み立てて渡す（DB からの解決は worker と統合テストの範囲）。
対象は examples/orders の複製（tmp_path）。
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import (
    ArtifactRevision,
    ArtifactScope,
    Bundle,
    BundleMember,
    ResolvedKnowledge,
    SpecContent,
)
from speclino.domain.policy import (
    PolicyApplicability,
    PolicyPackage,
    PolicyRevision,
    PolicyRule,
    PolicyVerifierRef,
)
from speclino.domain.spec_ir import SpecIR
from speclino.services import check_pipeline
from speclino.services.check_pipeline import CheckRequest, run_check, write_result
from speclino.services.knowledge_inputs import bundle_hash
from speclino.services.knowledge_registry import content_hash as artifact_hash
from speclino.spec import load_domain, load_spec_ir
from speclino.spec.uml import PARSER_VERSION, compile_spec

REPO = Path(__file__).resolve().parents[3]
ORDERS = REPO / "examples" / "orders"
DOMAIN = load_domain(ORDERS / ".speclino" / "domain.yaml")
PROJECT = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def block(*lines: str) -> str:
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


CLASSES = block(
    "%%@ id: vocab",
    "classDiagram",
    "    class OrderService {",
    "        +cancel_order(user, order)",
    "    }",
    "    class Inventory {",
    "        +restore(items)",
    "    }",
    "    class Audit {",
    "        +record(actor_id, action)",
    "    }",
    "    class Actor {",
    "        bool is_admin",
    "    }",
    "    class Order {",
    "        OrderStatus status",
    "    }",
    "    class OrderStatus {",
    "        <<enumeration>>",
    "        PENDING",
    "        SHIPPED",
    "        CANCELLED",
    "    }",
)


def sequence(entry: str = "OrderService.cancel_order") -> str:
    return block(
        "%%@ id: cancel",
        f"%%@ entry: {entry}",
        "sequenceDiagram",
        "    participant S as OrderService",
        "    participant I as Inventory",
        "    participant A as Audit",
        "    opt [o1] before.actor.is_admin and before.order.status == OrderStatus.PENDING",
        "        S->>I: [m1] restore(items)",
        "        S->>A: [m2] record(actor_id, action)",
        "    end",
    )


STATES = block(
    "%%@ id: status",
    "%%@ attribute: order.status",
    "stateDiagram-v2",
    "    [*] --> PENDING",
    "    PENDING --> CANCELLED: [t1] cancel",
    "    PENDING --> SHIPPED: [t2] ship",
)


def make_repo(root: Path, *, admin_only_restore: bool = False) -> Path:
    """examples/orders を根に持つ複製。bindings に図の名前の対応（上書き）を足す。"""
    app = root / "examples" / "orders" / "app"
    app.mkdir(parents=True)
    (root / "examples" / "__init__.py").write_text("")
    (root / "examples" / "orders" / "__init__.py").write_text("")
    for name in ("__init__.py", "models.py", "inventory.py", "audit.py", "service.py"):
        shutil.copy(ORDERS / "app" / name, app / name)
    shutil.copytree(ORDERS / ".speclino", root / ".speclino")
    shutil.copytree(ORDERS / "specs", root / "specs")
    if admin_only_restore:
        svc = app / "service.py"
        svc.write_text(
            svc.read_text().replace(
                "    inventory.restore(order.items)\n",
                "    if user.is_admin:\n        inventory.restore(order.items)\n",
            )
        )
    path = root / ".speclino" / "bindings.yaml"
    data = yaml.safe_load(path.read_text())
    data["behaviors"]["OrderService.cancel_order"] = data["behaviors"]["cancel_order"]
    ops = data["operations"]
    ops["Inventory.restore"] = dict(ops["inventory_restore_command"])
    ops["Audit.record"] = dict(ops["audit_record_command"])
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return root


def spec_revision(
    name: str,
    markdown: str,
    *,
    domain: DomainSpec | None = DOMAIN,
    legacy: list[SpecIR] | None = None,
) -> ArtifactRevision:
    result = compile_spec(markdown, domain=domain, legacy_ir=legacy or [])
    assert result.compiled is not None, result.diagnostics
    content = SpecContent(
        kind="spec",
        markdown=markdown,
        domain=domain,
        legacy_ir=legacy or [],
        scope=ArtifactScope(projects=[PROJECT]),
        compiled=result.compiled,
    )
    return ArtifactRevision(
        artifact_id=f"00000000-0000-4000-8000-{abs(hash(name)) % 10**12:012d}",
        workspace="w",
        kind="spec",
        name=name,
        revision=1,
        status="active",
        content=content,
        content_hash=artifact_hash(content),
        author="a",
        approver="b",
        created_at=NOW,
    )


def knowledge(
    *specs: ArtifactRevision, policy: PolicyRevision | None = None, generation: int = 3
) -> ResolvedKnowledge:
    bundle = Bundle(
        workspace="w",
        generation=generation,
        policy_revision=policy.revision if policy else None,
        members=[
            BundleMember(
                artifact_id=s.artifact_id,
                kind="spec",
                name=s.name,
                revision=s.revision,
                content_hash=s.content_hash,
            )
            for s in specs
        ],
        scope_hash="sha256:" + "0" * 64,
        signature="hmac-sha256:test",
        created_at=NOW,
    )
    return ResolvedKnowledge(bundle=bundle, specs=list(specs), policy=policy)


def request(root: Path, k: ResolvedKnowledge | None, **kw: Any) -> CheckRequest:
    base: dict[str, Any] = {
        "repo_root": root,
        "head_sha": "WORKTREE",
        "source_roots": (".",),
        "knowledge": k,
        "now": NOW,
        "run_id": "r1",
    }
    base.update(kw)
    return CheckRequest(**base)


UML = "# 注文の取消\n\n" + CLASSES + "\n" + sequence() + "\n" + STATES


def by_req(findings: list[Any]) -> dict[str, Any]:
    return {f.requirement_id: f for f in findings}


def test_bundle_specs_replace_git_specs(tmp_path: Path) -> None:
    """bundle の run はリポジトリの specs/ を読まず、bundle の仕様だけで判定する。"""
    root = make_repo(tmp_path / "repo")
    res = run_check(request(root, knowledge(spec_revision("orders-uml", UML))))
    ids = {f.requirement_id for f in res.findings.findings}
    assert not any(i.startswith("R-CANCEL-") for i in ids)  # Git の旧形式の仕様は読まない
    assert res.spec_irs == []
    assert {"vocab#OrderService", "cancel#OrderService.cancel_order", "status"} <= ids
    assert "KNOWLEDGE-LOCK-001" in ids


def test_sequence_obligation_verified_and_violated(tmp_path: Path) -> None:
    good = run_check(request(make_repo(tmp_path / "g"), knowledge(spec_revision("u", UML))))
    f = by_req(good.findings.findings)["cancel#OrderService.cancel_order"]
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert f.behavior_id == "OrderService.cancel_order"
    # bindings の別名のキー（Inventory.restore）が解析器のイベントの操作 ID にそろうので、呼出しを
    # 見落とさない（ずれると「呼んでいない」の偽の VIOLATED になる）

    bad_root = make_repo(tmp_path / "b", admin_only_restore=True)
    # 管理者だけが在庫を戻す実装は、図の条件（管理者・PENDING）では満たすので違反にならない。
    # 条件を所有者側にした図で違反を確かめる
    owner_seq = sequence().replace(
        "before.actor.is_admin and before.order.status == OrderStatus.PENDING",
        "not before.actor.is_admin and before.order.status == OrderStatus.PENDING",
    )
    md = "# 注文の取消\n\n" + CLASSES + "\n" + owner_seq
    bad = run_check(request(bad_root, knowledge(spec_revision("u", md))))
    f = by_req(bad.findings.findings)["cancel#OrderService.cancel_order"]
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect"), f.message


def test_unbound_entry_is_unknown(tmp_path: Path) -> None:
    md = (
        "# x\n\n"
        + CLASSES.replace(
            "+cancel_order(user, order)", "+cancel_order(user, order)\n        +refund(order)"
        )
        + "\n"
        + sequence("OrderService.refund")
    )
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(spec_revision("u", md))))
    f = by_req(res.findings.findings)["cancel#OrderService.refund"]
    assert (f.verdict, f.category) == ("UNKNOWN", "unsupported_analysis")
    assert "対応する振る舞いが無い" in f.message
    assert res.gate.status == "fail"


def test_unbound_operation_is_unknown(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    path = root / ".speclino" / "bindings.yaml"
    data = yaml.safe_load(path.read_text())
    del data["operations"]["Audit.record"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    res = run_check(request(root, knowledge(spec_revision("u", UML))))
    f = by_req(res.findings.findings)["cancel#OrderService.cancel_order"]
    assert f.verdict == "UNKNOWN" and "Audit.record" in f.message


def test_state_machine_checked_per_behavior(tmp_path: Path) -> None:
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(spec_revision("u", UML))))
    status = [f for f in res.findings.findings if f.requirement_id == "status"]
    assert {f.behavior_id for f in status} == {"cancel_order", "OrderService.cancel_order"}
    assert all(f.verdict == "VERIFIED_IN_SCOPE" for f in status), [f.message for f in status]


def test_checker_exception_is_unknown_and_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> list[object]:
        raise RuntimeError("壊れた")

    monkeypatch.setattr(check_pipeline, "check_structure", boom)
    res = run_check(
        request(
            make_repo(tmp_path / "r"),
            knowledge(spec_revision("u", UML)),
            github_repo_id=None,
        )
    )
    f = by_req(res.findings.findings)["vocab#OrderService"]
    assert f.verdict == "UNKNOWN" and "壊れた" in f.message
    assert res.gate.status == "fail"


def test_missing_obligation_finding_is_synthesized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """検査器が Finding を返さなかった義務は UNKNOWN（missing_finding）で補う（5.2）。"""
    monkeypatch.setattr(check_pipeline, "check_state_machine", lambda *a, **k: [])
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(spec_revision("u", UML))))
    f = by_req(res.findings.findings)["status"]
    assert (f.verdict, f.category) == ("UNKNOWN", "missing_finding")
    assert "status" in res.manifest.expected_requirement_ids


def _policy(
    rule_id: str = "MAKE-001", level: str = "mandatory", on_missing: str = "unknown"
) -> PolicyRevision:
    rule = PolicyRule(
        id=rule_id,
        version="1.0.0",
        level=level,  # type: ignore[arg-type]
        title="Makefile の標準ターゲット",
        text="setup/test/lint を持つこと",
        applies_to=PolicyApplicability(paths=["Makefile"]),
        verifier=PolicyVerifierRef(id="makefile.required_targets", version="1.0.0"),
        on_missing_input=on_missing,  # type: ignore[arg-type]
    )
    package = PolicyPackage(
        id="team-core", version="1.0.0", status="active", owner="w", rules=[rule]
    )
    return PolicyRevision(team="w", revision=7, status="active", package=package, author="a")


def test_team_policy_comes_from_the_bundle(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r")
    (root / "Makefile").write_text("test:\n\techo ok\n")
    k = knowledge(spec_revision("u", UML), policy=_policy())
    other = _policy("OTHER-001")
    res = run_check(request(root, k, team_policy=other))
    ids = {f.requirement_id.split(":", 1)[0] for f in res.findings.findings}
    assert "MAKE-001" in ids and "OTHER-001" not in ids
    assert (res.manifest.policy_team, res.manifest.policy_revision) == ("w", 7)
    assert "MAKE-001" in res.manifest.expected_requirement_ids


def test_missing_policy_finding_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from speclino.verifiers.registry import VerifierRegistry

    monkeypatch.setattr(VerifierRegistry, "run_package", lambda self, *a: [])
    root = make_repo(tmp_path / "r")
    res = run_check(request(root, knowledge(policy=_policy())))
    f = by_req(res.findings.findings)["MAKE-001"]
    assert (f.verdict, f.category) == ("UNKNOWN", "missing_finding")
    assert res.gate_levels["MAKE-001"] == "mandatory"


def test_policy_verifier_exception_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from speclino.verifiers.registry import VerifierRegistry

    def boom(self: object, *a: object) -> list[object]:
        raise LookupError("未登録の検証器")

    monkeypatch.setattr(VerifierRegistry, "run_package", boom)
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(policy=_policy())))
    f = by_req(res.findings.findings)["MAKE-001"]
    assert f.verdict == "UNKNOWN" and "未登録の検証器" in f.message


def test_lock_without_repo_id_is_unknown(tmp_path: Path) -> None:
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(), github_repo_id=None))
    f = by_req(res.findings.findings)["KNOWLEDGE-LOCK-001"]
    assert f.verdict == "UNKNOWN" and "リポジトリ ID" in f.message
    assert res.gate.status == "fail"


def test_lock_is_checked_against_the_pinned_bundle(tmp_path: Path) -> None:
    """lock が無い checkout は S68 の判定で UNKNOWN（必須）。"""
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(), github_repo_id=42))
    f = by_req(res.findings.findings)["KNOWLEDGE-LOCK-001"]
    assert f.verdict == "UNKNOWN" and f.code_refs[0].path == ".speclino/lock.json"


def test_git_specs_not_in_bundle_are_unknown(tmp_path: Path) -> None:
    res = run_check(request(make_repo(tmp_path / "r"), knowledge()))
    f = by_req(res.findings.findings)["KNOWLEDGE-SPEC-001"]
    assert f.verdict == "UNKNOWN" and "import-specs" in f.next_steps[0]


def test_manifest_records_the_bundle(tmp_path: Path) -> None:
    k = knowledge(spec_revision("u", UML), generation=5)
    res = run_check(request(make_repo(tmp_path / "r"), k))
    m = res.manifest
    assert m.bundle_generation == 5 and m.bundle_hash == bundle_hash(k.bundle)
    assert m.uml_parser_version == PARSER_VERSION
    assert m.created_at == NOW
    assert m.expected_requirement_ids[:5] == [
        "vocab#OrderService",
        "vocab#Inventory",
        "vocab#Audit",
        "cancel#OrderService.cancel_order",
        "status",
    ]
    assert m.expected_requirement_ids[-1] == "KNOWLEDGE-LOCK-001"


def test_legacy_spec_in_bundle_runs_through_the_engine(tmp_path: Path) -> None:
    """旧形式（compiled.legacy）は Git のときと同じエンジンで判定され、Finding が一致する。"""
    root = make_repo(tmp_path / "r")
    git = run_check(request(root, None))
    legacy = [load_spec_ir(root / "specs" / "ir" / "cancel_order.spec-ir.json")]
    markdown = (root / "specs" / "orders-cancel.md").read_text()
    rev = spec_revision("orders-cancel", markdown, legacy=legacy)
    res = run_check(request(root, knowledge(rev)))
    spec_findings = [f for f in res.findings.findings if not f.behavior_id.startswith("knowledge:")]
    assert [f.model_dump() for f in spec_findings] == [
        f.model_dump() for f in git.findings.findings
    ]
    assert [s.behavior_id for s in res.spec_irs] == ["cancel_order"]


def test_write_result_without_legacy_spec(tmp_path: Path) -> None:
    res = run_check(request(make_repo(tmp_path / "r"), knowledge(spec_revision("u", UML))))
    written = write_result(res, tmp_path / "out")
    assert "spec-ir.json" not in written
    assert {"run-manifest.json", "findings.json", "gate.json", "review.md"} <= set(written)


def test_selfcheck_still_reads_frozen_copy(tmp_path: Path) -> None:
    """knowledge が無い run（CLI のローカル実行・自己検証）は従来どおり Git の入力を読む。

    自己検証の検出テストは tests/fixtures/selfcheck の凍結コピーを使い続ける。
    """
    from tests.unit import test_self_verification as sv

    assert sv.FIXTURES == REPO / "tests" / "fixtures" / "selfcheck"
    assert sv.FROZEN_SPEC.behavior_id == "tool_gateway_invoke"
    res = run_check(request(make_repo(tmp_path / "r"), None))
    assert res.manifest.bundle_generation is None and res.manifest.bundle_hash is None
    assert res.manifest.expected_requirement_ids == []
    assert [s.behavior_id for s in res.spec_irs] == ["cancel_order"]
    assert not any(f.behavior_id.startswith("knowledge:") for f in res.findings.findings)


# ---------------------------------------------------------------- セキュリティ（base-trusted）


def _fake_call_head(root: Path) -> None:
    """攻撃: 在庫を戻す呼出しを消し、bindings で図の Inventory.restore を audit.record に向ける。"""
    svc = root / "examples" / "orders" / "app" / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "    inventory.restore(order.items)\n",
            "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        )
    )
    path = root / ".speclino" / "bindings.yaml"
    data = yaml.safe_load(path.read_text())
    data["operations"]["Inventory.restore"]["symbol"] = "examples.orders.app.audit.record"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))


def test_pr_bindings_cannot_fake_a_call(tmp_path: Path) -> None:
    """PR の bindings.yaml は判定に使わない（base の対応で判定し、変更は REVIEW）。"""
    base = make_repo(tmp_path / "base")
    head = make_repo(tmp_path / "head")
    _fake_call_head(head)
    k = knowledge(spec_revision("u", UML))
    # base が無ければ head が信頼側になり、書き換えた対応で偽の成立になりうる（攻撃の再現）
    naive = by_req(run_check(request(head, k)).findings.findings)
    assert naive["cancel#OrderService.cancel_order"].verdict == "VERIFIED_IN_SCOPE"

    res = run_check(request(head, k, base_root=base, base_sha="BASE"))
    f = by_req(res.findings.findings)
    assert f["cancel#OrderService.cancel_order"].verdict == "VIOLATED"
    notice = f["KNOWLEDGE-BINDINGS-001"]
    assert notice.verdict == "REVIEW" and ".speclino/bindings.yaml" in notice.message
    assert res.gate.status == "fail"


def test_pr_cannot_drop_project_policy_or_add_exception(tmp_path: Path) -> None:
    """プロジェクト固有の規約と免除は base から読む（PR で消す・足すことはできない）。"""
    base = make_repo(tmp_path / "base")
    head = make_repo(tmp_path / "head")
    team = _policy(on_missing="violation")
    from tests.unit.services.test_check_pipeline import _write_project_exceptions

    _write_project_exceptions(head)
    k = knowledge(policy=team)
    res = run_check(request(head, k, base_root=base, base_sha="BASE", today=NOW.date()))
    make = [x for x in res.findings.findings if x.requirement_id.startswith("MAKE-001")]
    assert make and all(x.verdict == "VIOLATED" for x in make)
    assert res.waivers == []
    assert (
        ".speclino/exceptions.yaml"
        in by_req(res.findings.findings)["KNOWLEDGE-BINDINGS-001"].message
    )
    # 同じ例外が base にあれば効く（取り込んだ後）
    _write_project_exceptions(base)
    merged = run_check(request(head, k, base_root=base, base_sha="BASE", today=NOW.date()))
    assert merged.waivers and "KNOWLEDGE-BINDINGS-001" not in by_req(merged.findings.findings)


def test_git_specs_notice_also_looks_at_base(tmp_path: Path) -> None:
    """PR で specs/ を消しても、取り込み待ちの通知は消えない。"""
    base = make_repo(tmp_path / "base")
    head = make_repo(tmp_path / "head")
    shutil.rmtree(head / "specs")
    res = run_check(request(head, knowledge(), base_root=base, base_sha="BASE"))
    assert by_req(res.findings.findings)["KNOWLEDGE-SPEC-001"].verdict == "UNKNOWN"
