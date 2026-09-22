"""既定上限で UNKNOWN になった要求を、エージェントの analyze_focus が決定的に解決する（デモ 2）。"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from speclino.adapters.llm.scripted import ScriptedLlm, call, text
from speclino.agent.permissions import AgentPermissions
from speclino.agent.tools import InvestigationContext
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.domain import AnalysisLimits, Bindings
from speclino.services.investigation import investigate
from tests.unit.analysis.test_path_analyzer import BINDINGS, DOMAIN, EXAMPLE, SPEC

# 在庫復元をヘルパー 3 段の奥に隠す（深さ 2 では展開できない）
DEEP_SERVICE = (EXAMPLE / "app" / "service.py").read_text().replace(
    "    inventory.restore(order.items)\n", "    _h1(order)\n"
) + textwrap.dedent(
    """

    def _h1(o: Order) -> None:
        _h2(o)


    def _h2(o: Order) -> None:
        _h3(o)


    def _h3(o: Order) -> None:
        inventory.restore(o.items)
    """
)


def make_ctx(tmp_path: Path, service_src: str, depth: int = 2) -> InvestigationContext:
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    for name in ("__init__.py", "models.py", "inventory.py", "audit.py"):
        shutil.copy(EXAMPLE / "app" / name, app / name)
    (app / "service.py").write_text(service_src.replace("examples.orders.app", "app"))
    b = BINDINGS.model_dump()
    b["behaviors"]["cancel_order"]["entrypoint"] = "app.service.cancel_order"
    for op in b["operations"].values():
        op["symbol"] = op["symbol"].replace("examples.orders.app", "app")
    bindings = Bindings.model_validate(b)
    limits = AnalysisLimits(depth=depth, max_paths=64, solver_timeout_ms=500)
    code = analyze_repo(tmp_path, "cancel_order", DOMAIN, bindings, limits=limits)
    findings = evaluate(SPEC, code, DOMAIN)
    return InvestigationContext(
        repo_root=tmp_path,
        domain=DOMAIN,
        bindings=bindings,
        contracts={},
        specs={"cancel_order": SPEC},
        findings=findings,
        code_irs={"cancel_order": code},
        limits=limits,
    )


def verdict(ctx: InvestigationContext, rid: str) -> str:
    return next(f.verdict for f in ctx.findings.findings if f.requirement_id == rid)


async def test_agent_not_started_when_everything_is_decided(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, (EXAMPLE / "app" / "service.py").read_text())
    res = await investigate(ctx, model=ScriptedLlm(steps=[[text("unused")]]))
    assert not res.started and "不要" in res.reason


async def test_agent_resolves_depth_limited_unknown_via_analyze_focus(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    ctx = make_ctx(tmp_path, DEEP_SERVICE, depth=2)
    assert verdict(ctx, "R-CANCEL-04") == "UNKNOWN"
    steps = [
        [call("policy_check")],
        [call("plan_record", steps=["R-CANCEL-04 の limit_depth を analyze_focus で解消"])],
        [call("analyze_focus", rule_id="R-CANCEL-04", depth=4)],
        [text("深さ 4 で再解析し、在庫復元の呼出しを確認した。")],
    ]
    res = await investigate(ctx, AgentPermissions(), ScriptedLlm(steps=steps))
    assert res.started
    assert verdict(ctx, "R-CANCEL-04") == "VERIFIED_IN_SCOPE"
    assert ctx.code_irs["cancel_order"].limits.depth == 4
    types = [e["type"] for e in res.events]
    assert types[:1] == ["agent.start"] and "plan" in types and "tool.analyze_focus" in types
    assert res.log is not None and res.log.budget["tool_calls"] == 3


async def test_agent_finds_violation_hidden_behind_helpers(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    buggy = DEEP_SERVICE.replace("    inventory.restore(o.items)\n", "    pass\n")
    assert buggy != DEEP_SERVICE
    ctx = make_ctx(tmp_path, buggy, depth=2)
    assert verdict(ctx, "R-CANCEL-04") == "UNKNOWN"
    steps = [[call("analyze_focus", rule_id="R-CANCEL-04", depth=4)], [text("done")]]
    await investigate(ctx, AgentPermissions(), ScriptedLlm(steps=steps))
    f = next(f for f in ctx.findings.findings if f.requirement_id == "R-CANCEL-04")
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")


async def test_repo_search_results_are_marked_untrusted(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    ctx = make_ctx(tmp_path, DEEP_SERVICE, depth=2)
    steps = [[call("repo_search", query="inventory.restore")], [text("done")]]
    res = await investigate(ctx, AgentPermissions(), ScriptedLlm(steps=steps))
    search = next(e for e in res.events if e["type"] == "tool.repo_search")
    assert search["payload"]["hits"] >= 1
    # ツール結果は untrusted フラグ付きでモデルに返る
    tool_result = next(
        e
        for e in res.log.events
        if e["function_responses"]  # type: ignore[union-attr]
    )
    assert tool_result["function_responses"][0]["name"] == "repo_search"


async def test_unsupported_syntax_cannot_be_resolved_by_agent(tmp_path: Path) -> None:
    ScriptedLlm.reset()
    looped = (
        (EXAMPLE / "app" / "service.py")
        .read_text()
        .replace(
            "    inventory.restore(order.items)\n",
            "    for item in order.items:\n        inventory.restore(item)\n",
        )
    )
    ctx = make_ctx(tmp_path, looped)
    steps = [
        [call("analyze_focus", rule_id="R-CANCEL-04", depth=4)],
        [text("対応構文への書き直しが必要")],
    ]
    await investigate(ctx, AgentPermissions(), ScriptedLlm(steps=steps))
    assert verdict(ctx, "R-CANCEL-04") == "UNKNOWN"
