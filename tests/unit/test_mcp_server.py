"""MCP サーバー（S33）: 語彙の読出し、Bearer 認証、Streamable HTTP でのツール一覧。"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from mcp.client import Client

from speclino.api.app import create_app
from speclino.domain.policy import (
    PolicyApplicability,
    PolicyPackage,
    PolicyRevision,
    PolicyRule,
)
from speclino.mcp_server.server import allowed_hosts, build_mcp, skill_body, skill_instructions
from speclino.services.check_pipeline import _run_policies
from speclino.services.mcp_context import read_context
from speclino.settings import Settings

REPO = Path(__file__).resolve().parents[2]
# tests/fixtures/selfcheck に凍結済みの Spec IR（R-TOOL-01 / R-GATE-01 を含む）を使う。
# live の specs/ir に依存しないので、デモが Spec IR を消しても振る舞いを検証できる。
SELFCHECK_FIXTURES = REPO / "tests" / "fixtures" / "selfcheck"


def test_read_context_lists_vocabulary_policies_and_rules(tmp_path: Path) -> None:
    """read_context（MCP の context_get）の抽出結果を検証する。

    domain.yaml / bindings.yaml / policy はこの関数の実装上そのまま yaml.safe_load されるだけ
    （pydantic 検証を通らない）なので最小限の内容で足りる。specs/ir だけは load_spec_ir を通るため、
    凍結済みで型的に妥当な Spec IR（R-TOOL-01 / R-GATE-01 を含む）を再利用する。
    """
    speclino_dir = tmp_path / ".speclino"
    (speclino_dir / "policy").mkdir(parents=True)
    (speclino_dir / "domain.yaml").write_text(
        "variables:\n  has_findings: boolean\n", encoding="utf-8"
    )
    (speclino_dir / "bindings.yaml").write_text("behaviors:\n  gate_decide: {}\n", encoding="utf-8")
    (speclino_dir / "policy" / "policy.yaml").write_text(
        "rules:\n"
        + "".join(
            f"  - id: {rid}\n    level: mandatory\n    title: {rid} fixture\n"
            for rid in ("MAKE-001", "SPEC-FMT-001", "CI-001")
        ),
        encoding="utf-8",
    )
    ir_dir = tmp_path / "specs" / "ir"
    ir_dir.mkdir(parents=True)
    for name in ("tool_gateway.spec-ir.json", "gate_decide.spec-ir.json"):
        shutil.copy(SELFCHECK_FIXTURES / name, ir_dir / name)

    ctx = read_context(tmp_path)
    assert "has_findings" in ctx["domain"]["variables"]
    assert "gate_decide" in ctx["bindings"]["behaviors"]
    assert {p["id"] for p in ctx["policies"]} >= {"MAKE-001", "SPEC-FMT-001", "CI-001"}
    by_id = {r["id"]: r for r in ctx["rules"]}
    assert by_id["R-TOOL-01"]["approved"] and by_id["R-TOOL-01"]["spec"] == "specs/SPEC-TOOL-001.md"
    assert by_id["R-GATE-01"]["behavior_id"] == "gate_decide"


def test_allowed_hosts_include_public_host_and_localhost() -> None:
    hosts = allowed_hosts("https://speclino-web-abc.a.run.app")
    assert "speclino-web-abc.a.run.app" in hosts and "localhost:*" in hosts


@pytest.mark.asyncio
async def test_tools_are_exposed_in_process() -> None:
    async with Client(build_mcp(lambda: None)) as client:  # type: ignore[arg-type, return-value]
        tools = await client.list_tools()
    assert {t.name for t in tools.tools} == {
        "context_get",
        "verification_start",
        "verification_get",
        "sync_status",
        "knowledge_pull",
        "spec_get",
        "skill_get",
        "spec_suggest",
    }


def test_skill_instructions_are_at_most_2kb() -> None:
    """MCP の instructions は 2KB 以内（S57、完了条件）。"""
    assert len(skill_instructions().encode("utf-8")) <= 2048


@pytest.mark.asyncio
async def test_prompt_returns_same_text_as_skill_md_body() -> None:
    """`speclino_guide` prompt は SKILL.md 本文をそのまま返す（Claude Code 以外向け、S57）。

    正本は 1 つ（`plugins/speclino/skills/speclino-spec-authoring/SKILL.md`）なので、
    instructions とこの prompt が同じファイルからずれずに作られていることを確かめる。
    """
    async with Client(build_mcp(lambda: None)) as client:  # type: ignore[arg-type, return-value]
        result = await client.get_prompt("speclino_guide")
    [message] = result.messages
    assert message.content.text == skill_body()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_http_mount_rejects_missing_and_malformed_tokens() -> None:
    """MCP は常にマウントされ、トークンが無い・形式が違うものは DB を引かずに 401（S54）。

    有効なトークンでツールが使えることは統合テスト（tests/integration/services/test_mcp_flow.py）。
    """
    app = create_app(Settings(env="local", role="all", code_host_kind="local"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c,
    ):
        for headers in ({}, {"Authorization": "Bearer not-a-speclino-token"}):
            denied = await c.post("/mcp", json={}, headers=headers)
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "unauthenticated"


def _team_revision(*rules: PolicyRule) -> PolicyRevision:
    package = PolicyPackage(
        id="team-core", version="1", status="active", owner="acme", rules=list(rules)
    )
    return PolicyRevision(team="acme", revision=3, status="active", package=package, author="a")


def _advisory(rule_id: str) -> PolicyRule:
    return PolicyRule(
        id=rule_id,
        version="1",
        level="advisory",
        title=f"{rule_id} title",
        text=f"{rule_id} text",
        applies_to=PolicyApplicability(paths=["**/*.py"]),
        on_missing_input="skipped",
    )


def test_read_context_merges_team_policy_like_the_pipeline(tmp_path: Path) -> None:
    """S44: チーム規約を先に並べ、同じ id のプロジェクト規約は無視して、その事実を返す。

    エージェントが読む規約とゲートが照らす規約がずれないよう、合わせた後の id の集合が
    `check_pipeline._run_policies` の `rules_by_id` と一致することも確かめる。
    """
    policy_dir = tmp_path / ".speclino" / "policy"
    policy_dir.mkdir(parents=True)
    shutil.copy(REPO / ".speclino" / "policy" / "speclino-core.yaml", policy_dir)
    team = _team_revision(_advisory("MAKE-001"), _advisory("TEAM-001"))

    ctx = read_context(tmp_path, team)

    assert ctx["team_policy"] == {"team": "acme", "revision": 3}
    assert ctx["ignored_project_policies"] == ["MAKE-001"]
    by_id = {p["id"]: p for p in ctx["policies"]}
    assert [p["id"] for p in ctx["policies"][:2]] == ["MAKE-001", "TEAM-001"]
    assert by_id["MAKE-001"]["origin"] == "team" and by_id["MAKE-001"]["verifier"] is None
    assert by_id["SPEC-FMT-001"]["origin"] == "project"
    assert by_id["SPEC-FMT-001"]["verifier"] is not None
    assert set(by_id) == set(_run_policies(tmp_path, team).rules_by_id)


def test_read_context_without_team_policy_lists_project_rules_only(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".speclino" / "policy"
    policy_dir.mkdir(parents=True)
    shutil.copy(REPO / ".speclino" / "policy" / "speclino-core.yaml", policy_dir)

    ctx = read_context(tmp_path)

    assert ctx["team_policy"] is None and ctx["ignored_project_policies"] == []
    assert {p["origin"] for p in ctx["policies"]} == {"project"}


def test_read_context_keeps_the_first_project_rule_like_the_pipeline(tmp_path: Path) -> None:
    """同じ id のプロジェクト規約が複数ファイルにあるとき、検証と同じく先のファイルだけを採る。"""
    policy_dir = tmp_path / ".speclino" / "policy"
    policy_dir.mkdir(parents=True)
    core = REPO / ".speclino" / "policy" / "speclino-core.yaml"
    shutil.copy(core, policy_dir / "a.yaml")
    shutil.copy(core, policy_dir / "b.yml")

    ctx = read_context(tmp_path)

    ids = [p["id"] for p in ctx["policies"]]
    assert len(ids) == len(set(ids))
    assert set(ctx["ignored_project_policies"]) == set(ids)
    assert set(ids) == set(_run_policies(tmp_path, None).rules_by_id)
