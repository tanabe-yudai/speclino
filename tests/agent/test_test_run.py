"""test_run（S31）: junit のパースと、観測証拠の判定への反映（fake sandbox）。Docker は opt-in。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from speclino.adapters.llm.scripted import ScriptedLlm, call, text
from speclino.adapters.sandbox.docker_local import DockerSandbox
from speclino.agent.permissions import AgentPermissions
from speclino.agent.tools import InvestigationContext
from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.domain import Bindings, DomainSpec, SpecIR
from speclino.ports.sandbox import SandboxResult
from speclino.services.investigation import investigate
from speclino.spec import load_bindings, load_domain, load_spec_ir
from speclino.trace.junit import parse_junit

REPO = Path(__file__).resolve().parents[2]
ORDERS = REPO / "examples" / "orders"
JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="3">
<testcase classname="examples.orders.tests.test_spec_cancel"
 name="test_owner_cancel_restores_inventory_once" file="examples/orders/tests/test_spec_cancel.py">
<failure message="assert [] == [('sku-1',)]">x</failure></testcase>
<testcase classname="examples.orders.tests.test_spec_cancel"
 name="test_admin_cancel_restores_inventory_once" file="examples/orders/tests/test_spec_cancel.py"/>
<testcase classname="examples.orders.tests.test_spec_cancel" name="test_stranger_gets_403"
 file="examples/orders/tests/test_spec_cancel.py"><skipped message="no"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit_is_deterministic() -> None:
    outcomes = parse_junit(JUNIT)
    assert [(o.test_id.split("::")[1], o.status) for o in outcomes] == [
        ("test_admin_cancel_restores_inventory_once", "passed"),
        ("test_owner_cancel_restores_inventory_once", "failed"),
        ("test_stranger_gets_403", "skipped"),
    ]
    assert outcomes[1].message.startswith("assert")


@dataclass
class FakeSandbox:
    junit: str | None
    calls: list[list[str]]
    timed_out: bool = False

    def run_tests(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str], timeout_s: int
    ) -> SandboxResult:
        self.calls.append(test_ids)
        return SandboxResult(1 if self.junit else 2, self.junit, "", self.timed_out, "fake")


def unresolved_ctx(tmp_path: Path, sandbox: object) -> InvestigationContext:
    """在庫復元をループで呼ぶ（静的には UNKNOWN）head と、正しい base。"""
    for name in ("app", "specs", ".speclino", "tests"):
        src = ORDERS / name
        dst = tmp_path / "head" / "examples" / "orders" / name
        shutil.copytree(src, dst)
    (tmp_path / "head" / "examples" / "__init__.py").write_text("")
    (tmp_path / "head" / "examples" / "orders" / "__init__.py").write_text("")
    shutil.copytree(tmp_path / "head", tmp_path / "base")
    svc = tmp_path / "head" / "examples" / "orders" / "app" / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "    inventory.restore(order.items)\n",
            "    for item in order.items:\n        inventory.restore(item)\n",
        )
    )
    project = tmp_path / "head" / "examples" / "orders"
    domain: DomainSpec = load_domain(project / ".speclino" / "domain.yaml")
    bindings: Bindings = load_bindings(project / ".speclino" / "bindings.yaml")
    spec: SpecIR = load_spec_ir(project / "specs" / "ir" / "cancel_order.spec-ir.json")
    code = analyze_repo(tmp_path / "head", "cancel_order", domain, bindings, limits=DEFAULT_LIMITS)
    findings = evaluate(spec, code, domain)
    return InvestigationContext(
        repo_root=tmp_path / "head",
        domain=domain,
        bindings=bindings,
        contracts={},
        specs={"cancel_order": spec},
        findings=findings,
        code_irs={"cancel_order": code},
        limits=DEFAULT_LIMITS,
        base_root=tmp_path / "base",
        sandbox=sandbox,  # type: ignore[arg-type]
    )


def verdict(ctx: InvestigationContext, rid: str) -> tuple[str, str | None]:
    f = next(f for f in ctx.findings.findings if f.requirement_id == rid)
    return f.verdict, f.test_observation


async def test_failed_declared_test_yields_observed_violation(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    fake = FakeSandbox(JUNIT, [])
    ctx = unresolved_ctx(tmp_path, fake)
    assert verdict(ctx, "R-CANCEL-04")[0] == "UNKNOWN"
    perms = AgentPermissions(test_run_enabled=True)
    res = await investigate(
        ctx, perms, ScriptedLlm(steps=[[call("test_run", rule_id="R-CANCEL-04")], [text("done")]])
    )
    assert res.started and fake.calls == [
        [
            "examples/orders/tests/test_spec_cancel.py::test_owner_cancel_restores_inventory_once",
            "examples/orders/tests/test_spec_cancel.py::test_admin_cancel_restores_inventory_once",
        ]
    ]
    f = next(f for f in ctx.findings.findings if f.requirement_id == "R-CANCEL-04")
    assert (f.verdict, f.category, f.test_observation) == (
        "VIOLATED",
        "missing_effect",
        "CONTRADICTED",
    )
    assert f.counterexample is not None and f.counterexample.kind == "observed"


async def test_passing_declared_tests_yield_dynamic_verified(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    passed = JUNIT.replace("<failure message=\"assert [] == [('sku-1',)]\">x</failure>", "")
    ctx = unresolved_ctx(tmp_path, FakeSandbox(passed, []))
    await investigate(
        ctx,
        AgentPermissions(test_run_enabled=True),
        ScriptedLlm(steps=[[call("test_run", rule_id="R-CANCEL-04")], [text("done")]]),
    )
    f = next(f for f in ctx.findings.findings if f.requirement_id == "R-CANCEL-04")
    assert (f.verdict, f.test_observation) == ("VERIFIED_IN_SCOPE", "OBSERVED")
    assert "経路の範囲" in (f.analysis_scope or "")


async def test_error_or_timeout_does_not_change_verdict(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    ctx = unresolved_ctx(tmp_path, FakeSandbox(None, [], timed_out=True))
    await investigate(
        ctx,
        AgentPermissions(test_run_enabled=True),
        ScriptedLlm(steps=[[call("test_run", rule_id="R-CANCEL-04")], [text("done")]]),
    )
    assert verdict(ctx, "R-CANCEL-04") == ("UNKNOWN", None)


async def test_test_run_denied_when_not_enabled(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    fake = FakeSandbox(JUNIT, [])
    ctx = unresolved_ctx(tmp_path, fake)
    res = await investigate(
        ctx,
        AgentPermissions(test_run_enabled=False),
        ScriptedLlm(steps=[[call("test_run", rule_id="R-CANCEL-04")], [text("done")]]),
    )
    assert fake.calls == [] and res.log is not None and res.log.denied[0]["tool"] == "test_run"


@pytest.mark.skipif(
    os.environ.get("SPECLINO_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
    reason="SPECLINO_DOCKER_TESTS=1 と docker が必要",
)
def test_docker_sandbox_runs_declared_tests_without_network(tmp_path: Path) -> None:
    subprocess.run(
        ["docker", "build", "-q", "-t", "speclino-sandbox-runner:latest", "docker/sandbox"],
        check=True,
        cwd=REPO,
        capture_output=True,
    )
    ctx = unresolved_ctx(tmp_path, None)
    sb = DockerSandbox()
    ids = [
        "examples/orders/tests/test_spec_cancel.py::test_owner_cancel_restores_inventory_once",
        "examples/orders/tests/test_spec_cancel.py::test_stranger_gets_403",
    ]
    res = sb.run_tests(ctx.repo_root, tmp_path / "base", ids, 120)
    assert res.junit_xml is not None and not res.timed_out
    outcomes = {o.test_id.split("::")[1]: o.status for o in parse_junit(res.junit_xml)}
    # head はループで item ごとに restore するので、タプル 1 回の期待と食い違い失敗する
    assert outcomes["test_owner_cancel_restores_inventory_once"] == "failed"
    assert outcomes["test_stranger_gets_403"] == "passed"
