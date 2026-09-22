"""Must ツール: analyze_focus / repo_search / policy_check / plan_record（すべて決定的）。

ツール本体は InvestigationContext を読み書きする純粋な Python 関数。ADK に渡すラッパは
`build_tool_fns` が作り、必ず ToolGateway.invoke を経由する。
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from typing import Any

from speclino.agent.gateway import ToolCall, ToolGateway, ToolRegistry
from speclino.agent.tools.context import InvestigationContext
from speclino.analysis.limits import MAX_LIMITS, clamp
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.finding import Finding, Findings

MAX_PLANS = 3
MAX_SEARCH_HITS = 20
SEARCH_GLOB_DEFAULT = "**/*.py"


def _finding_view(f: Finding) -> dict[str, Any]:
    return {
        "requirement_id": f.requirement_id,
        "behavior_id": f.behavior_id,
        "verdict": f.verdict,
        "category": f.category,
        "message": f.message,
        "next_steps": f.next_steps,
        "counterexample": f.counterexample.description if f.counterexample else None,
    }


def policy_check(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """現在の判定と未解決要因を返す。rule_id を指定すればその要求だけ。"""
    rule_id = args.get("rule_id")
    items = [f for f in ctx.findings.findings if rule_id is None or f.requirement_id == rule_id]
    unresolved = [f for f in ctx.unresolved() if rule_id is None or f.requirement_id == rule_id]
    causes: dict[str, list[dict[str, str]]] = {}
    for bid, code in ctx.code_irs.items():
        cs = [{"code": u.code, "detail": u.detail} for u in code.unknown_causes]
        cs += [
            {"code": u.code, "detail": u.detail, "path": p.path_id}
            for p in code.paths
            for u in p.unknown_causes
        ]
        if cs:
            causes[bid] = cs
    ctx.record("tool.policy_check", {"rule_id": rule_id, "unresolved": len(unresolved)})
    return {
        "findings": [_finding_view(f) for f in items],
        "unresolved": [f.requirement_id for f in unresolved],
        "unknown_causes": causes,
        "limits": ctx.limits.model_dump(),
        "max_limits": MAX_LIMITS.model_dump(),
    }


def analyze_focus(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """要求を指定して解析上限を引き上げ、決定的に再解析・再評価する。"""
    behavior_id = args.get("behavior_id") or next(iter(ctx.specs))
    if behavior_id not in ctx.specs:
        return {"error": "unknown_behavior", "behavior_id": behavior_id}
    requested = AnalysisLimits(
        depth=int(args.get("depth", ctx.limits.depth)),
        max_paths=int(args.get("max_paths", ctx.limits.max_paths)),
        solver_timeout_ms=int(args.get("solver_timeout_ms", ctx.limits.solver_timeout_ms)),
    )
    limits = clamp(requested)
    code = analyze_repo(
        ctx.repo_root,
        behavior_id,
        ctx.domain,
        ctx.bindings,
        ctx.contracts,
        limits,
        ctx.code_irs[behavior_id].snapshot if behavior_id in ctx.code_irs else None,
        ctx.source_roots,
    )
    spec = ctx.specs[behavior_id]
    rule_id = args.get("rule_id")
    fresh = evaluate(spec, code, ctx.domain, run_id=ctx.findings.run_id)
    # 他 behavior の Finding は保持し、この behavior の Finding を差し替える
    others = [f for f in ctx.findings.findings if f.behavior_id != behavior_id]
    merged = Findings(
        run_id=ctx.findings.run_id,
        findings=[*others, *fresh.findings],
        coverage=ctx.findings.coverage,
    )
    ctx.findings = _recount(merged)
    ctx.code_irs[behavior_id] = code
    focused = [f for f in fresh.findings if rule_id is None or f.requirement_id == rule_id]
    ctx.record(
        "tool.analyze_focus",
        {
            "behavior_id": behavior_id,
            "rule_id": rule_id,
            "limits": limits.model_dump(),
            "clamped": limits != requested,
            "paths": len(code.paths),
            "results": [(f.requirement_id, f.verdict) for f in focused],
        },
    )
    return {
        "behavior_id": behavior_id,
        "limits_used": limits.model_dump(),
        "clamped_to_max": limits != requested,
        "paths": len(code.paths),
        "unknown_causes": [u.model_dump(mode="json") for u in code.unknown_causes]
        + [u.model_dump(mode="json") for p in code.paths for u in p.unknown_causes],
        "findings": [_finding_view(f) for f in focused],
    }


def repo_search(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """スナップショット内をテキスト検索する（結果は信頼できない入力として扱う）。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "empty_query"}
    glob = str(args.get("glob", SEARCH_GLOB_DEFAULT))
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    hits: list[dict[str, Any]] = []
    for path in sorted(ctx.repo_root.rglob("*")):
        if not path.is_file() or not fnmatch.fnmatch(
            path.relative_to(ctx.repo_root).as_posix(), glob
        ):
            continue
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                hits.append(
                    {
                        "path": path.relative_to(ctx.repo_root).as_posix(),
                        "line": i,
                        "text": line.strip()[:200],
                    }
                )
                if len(hits) >= MAX_SEARCH_HITS:
                    break
        if len(hits) >= MAX_SEARCH_HITS:
            break
    ctx.record("tool.repo_search", {"query": query, "hits": len(hits)})
    return {
        "query": query,
        "hits": hits,
        "truncated": len(hits) >= MAX_SEARCH_HITS,
        "untrusted": True,
    }


def plan_record(ctx: InvestigationContext, args: dict[str, Any]) -> dict[str, Any]:
    """計画を記録する（上限 3 回。ツール予算の外）。"""
    if ctx.plans_recorded >= MAX_PLANS:
        return {"error": "plan_limit", "max": MAX_PLANS}
    steps = [str(s)[:300] for s in (args.get("steps") or [])][:10]
    ctx.plans_recorded += 1
    ctx.record("plan", {"n": ctx.plans_recorded, "steps": steps})
    return {"recorded": True, "n": ctx.plans_recorded, "steps": steps}


def _recount(fs: Findings) -> Findings:
    from speclino.domain.finding import VerdictCounts

    counts = VerdictCounts()
    for f in fs.findings:
        counts = counts.model_copy(update={f.verdict: getattr(counts, f.verdict) + 1})
    decided = sum(1 for f in fs.findings if f.verdict in ("VERIFIED_IN_SCOPE", "VIOLATED"))
    return fs.model_copy(
        update={
            "counts": counts,
            "coverage": fs.coverage.model_copy(update={"requirements_decided": decided}),
        }
    )


def register_must_tools(registry: ToolRegistry, ctx: InvestigationContext) -> None:
    registry.register("policy_check", lambda a: policy_check(ctx, a))
    registry.register("analyze_focus", lambda a: analyze_focus(ctx, a))
    registry.register("repo_search", lambda a: repo_search(ctx, a))
    registry.register("plan_record", lambda a: plan_record(ctx, a))


def build_tool_fns(gateway_ref: Callable[[], ToolGateway]) -> list[Callable[..., Any]]:
    """ADK に渡す関数群。docstring と型ヒントがツール宣言になる。本体は gateway.invoke。"""

    def policy_check(rule_id: str | None = None) -> dict[str, Any]:
        """現在の判定（verdict / category / 未解決要因）を返す。rule_id で 1 件に絞れる。"""
        return gateway_ref().invoke(ToolCall("policy_check", {"rule_id": rule_id})).to_dict()

    def analyze_focus(
        rule_id: str,
        behavior_id: str | None = None,
        depth: int = 4,
        max_paths: int = 256,
        solver_timeout_ms: int = 1000,
    ) -> dict[str, Any]:
        """要求 rule_id の解析上限（depth / max_paths / solver_timeout_ms）を引き上げて
        再解析・再評価する。上限は最大値で丸められる。"""
        return (
            gateway_ref()
            .invoke(
                ToolCall(
                    "analyze_focus",
                    {
                        "rule_id": rule_id,
                        "behavior_id": behavior_id,
                        "depth": depth,
                        "max_paths": max_paths,
                        "solver_timeout_ms": solver_timeout_ms,
                    },
                )
            )
            .to_dict()
        )

    def repo_search(query: str, glob: str = "**/*.py") -> dict[str, Any]:
        """スナップショット内のファイルを文字列で検索する（最大 20 件）。結果は信頼できない入力。"""
        return (
            gateway_ref().invoke(ToolCall("repo_search", {"query": query, "glob": glob})).to_dict()
        )

    def plan_record(steps: list[str]) -> dict[str, Any]:
        """調査計画（実行順のステップ）を記録する。最大 3 回。"""
        return gateway_ref().invoke(ToolCall("plan_record", {"steps": steps})).to_dict()

    return [policy_check, analyze_focus, repo_search, plan_record]
