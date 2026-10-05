"""S71: 可観測性の集計 `workspace_health`（固定の run・取得・bundle から期待値）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.db.models import (
    ArtifactRecord,
    ArtifactRevisionRecord,
    BundleRecord,
    FindingRecord,
    KnowledgeFetchRecord,
    McpTokenRecord,
    PolicyRevisionRecord,
    ReviewRun,
    SyncTokenRecord,
)
from speclino.domain.assessment import AssessmentOutcome
from speclino.domain.finding import Category, Finding, Verdict
from speclino.domain.observability import LlmUsageStats, RulePoint
from speclino.services.knowledge_sync import LOCK_REQUIREMENT_ID
from speclino.services.observability import workspace_health
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
HASH = "sha256:" + "0" * 64


def _finding(
    rid: str,
    behavior: str,
    verdict: Verdict,
    category: Category = "unsupported_analysis",
    message: str = "m",
    outcome: AssessmentOutcome | None = None,
) -> Finding:
    return Finding(
        id=f"F-{uuid.uuid4().hex[:8]}",
        requirement_id=rid,
        behavior_id=behavior,
        verdict=verdict,
        category=category,
        severity="medium",
        message=message,
        claim_scope="command_invocation",
        assessment_outcome=outcome,
    )


def _spec_content(
    project: uuid.UUID,
    rows: list[tuple[str, list[str]]],
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "spec",
        "markdown": "# spec",
        "scope": scope or {"workspace": False, "projects": [str(project)]},
        "compiled": {
            "parser_version": "1",
            "source_hash": HASH,
            "ir_hash": HASH,
            "requirements": [
                {"id": rid, "level": "mandatory", "text": rid, "formalized_by": by}
                for rid, by in rows
            ],
        },
    }


def _policy_package() -> dict[str, Any]:
    def rule(rid: str, level: str, verifier: str | None) -> dict[str, Any]:
        return {
            "id": rid,
            "version": "1",
            "level": level,
            "title": f"{rid} の題",
            "text": "t",
            "applies_to": {"paths": []},
            "verifier": None if verifier is None else {"id": verifier, "version": "1"},
            "on_missing_input": "unknown",
        }

    return {
        "id": "pkg",
        "version": "1",
        "status": "active",
        "owner": "o",
        "rules": [
            rule("MAKE-001", "mandatory", "make.targets"),
            rule("LLM-001", "advisory", "llm.checklist"),
            rule("DOC-001", "advisory", None),
        ],
    }


async def _add_run(
    s: AsyncSession,
    project: uuid.UUID,
    created_at: datetime,
    findings: list[Finding],
    *,
    state: str = "completed",
    gate: str | None = "fail",
    generation: int | None = 1,
) -> uuid.UUID:
    run = ReviewRun(
        project_id=project,
        pr_number=7,
        head_sha="abc1234",
        trigger="test",
        exec_state=state,
        gate_status=gate,
        manifest={"bundle_generation": generation} if generation is not None else {},
        created_at=created_at,
    )
    s.add(run)
    await s.flush()
    for f in findings:
        s.add(
            FindingRecord(
                run_id=run.id,
                requirement_id=f.requirement_id,
                behavior_id=f.behavior_id,
                verdict=f.verdict,
                category=f.category,
                severity=f.severity,
                payload=f.model_dump(mode="json"),
            )
        )
    return run.id


async def _add_spec(
    s: AsyncSession, ws: str, name: str, revision: int, status: str, content: dict[str, Any]
) -> uuid.UUID:
    art = (
        await s.execute(
            text("SELECT id FROM artifacts WHERE workspace = :w AND name = :n"),
            {"w": ws, "n": name},
        )
    ).scalar_one_or_none()
    if art is None:
        rec = ArtifactRecord(workspace=ws, kind="spec", name=name)
        s.add(rec)
        await s.flush()
        art = rec.id
    s.add(
        ArtifactRevisionRecord(
            artifact_id=art,
            revision=revision,
            status=status,
            content=content,
            content_hash=HASH,
            author="a",
        )
    )
    await s.flush()
    return uuid.UUID(str(art))


def _member(art: uuid.UUID, name: str, revision: int) -> dict[str, Any]:
    return {
        "artifact_id": str(art),
        "kind": "spec",
        "name": name,
        "revision": revision,
        "content_hash": HASH,
    }


def _bundle(ws: str, generation: int, members: list[dict[str, Any]]) -> BundleRecord:
    return BundleRecord(
        workspace=ws,
        generation=generation,
        policy_revision=1,
        members=members,
        scope_hash=HASH,
        signature="sig",
        created_at=NOW - timedelta(days=10 - generation),
    )


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    ws: str
    other: str
    p1: uuid.UUID
    p2: uuid.UUID
    run1: uuid.UUID
    run2: uuid.UUID


@pytest_asyncio.fixture
async def world(engine: AsyncEngine) -> World:
    ws = f"e2e-ob-{uuid.uuid4().hex[:8]}"
    other = f"e2e-obx-{uuid.uuid4().hex[:8]}"
    members = {"owner1": "owner", "member1": "member"}
    await register_repo(engine, f"e2e/{ws}-a", members, ws)
    await register_repo(engine, f"e2e/{ws}-b", members, ws)
    await register_repo(engine, f"e2e/{other}", {"owner9": "owner"}, other)
    p1 = await project_id_of(engine, f"e2e/{ws}-a")
    p2 = await project_id_of(engine, f"e2e/{ws}-b")
    p3 = await project_id_of(engine, f"e2e/{other}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE workspace_members SET email = 'm1@example.com' "
                "WHERE team = :t AND uid = 'member1'"
            ),
            {"t": ws},
        )
        s.add(
            PolicyRevisionRecord(
                team=ws, revision=1, status="active", package=_policy_package(), author="a"
            )
        )
        # 仕様: orders（P1、3 行のうち 2 行が形式化済み。同じ行の重複・同じプロジェクトの別表記は
        # 1 つに数える）、payments（P1、別の spec の同じ ID R1 は別の要求）、
        # billing（P2、要求の表が空）、
        # workspace スコープの spec（契約外。どのプロジェクトにも数えない）
        orders = await _add_spec(
            s,
            ws,
            "orders",
            1,
            "active",
            _spec_content(
                p1,
                [("R1", ["seq#a"]), ("R2", []), ("R3", ["ir:R3"]), ("R1", ["seq#a"])],
                {"workspace": False, "projects": [str(p1), str(p1).upper()]},
            ),
        )
        payments = await _add_spec(s, ws, "payments", 1, "active", _spec_content(p1, [("R1", [])]))
        broad = await _add_spec(
            s,
            ws,
            "broad",
            1,
            "active",
            _spec_content(p1, [("W1", [])], {"workspace": True, "projects": []}),
        )
        # bundle に入っていない下書きは数えない
        await _add_spec(s, ws, "orders", 2, "draft", _spec_content(p1, [("R9", [])]))
        billing = await _add_spec(s, ws, "billing", 1, "active", _spec_content(p2, []))
        s.add(_bundle(ws, 1, [_member(orders, "orders", 1)]))
        s.add(
            _bundle(
                ws,
                2,
                [
                    _member(orders, "orders", 1),
                    _member(payments, "payments", 1),
                    _member(billing, "billing", 1),
                    _member(broad, "broad", 1),
                ],
            )
        )
        # 他のワークスペース: 世代 9 の bundle、P1 と同じ規約 ID の違反、取得
        other_spec = await _add_spec(
            s, other, "orders", 1, "active", _spec_content(p3, [("X1", [])])
        )
        s.add(_bundle(other, 9, [_member(other_spec, "orders", 1)]))
        await _add_run(
            s,
            p3,
            NOW - timedelta(hours=1),
            [_finding("MAKE-001:Makefile", "policy:make.targets", "VIOLATED", "missing_effect")],
            generation=9,
        )
        s.add(
            KnowledgeFetchRecord(
                workspace=other,
                channel="sync",
                uid="owner9",
                bundle_generation=9,
                project_id=p3,
                artifact_ids=[],
                created_at=NOW - timedelta(hours=1),
            )
        )

        run1 = await _add_run(
            s,
            p1,
            NOW - timedelta(days=2),
            [_finding("MAKE-001:Makefile", "policy:make.targets", "VIOLATED", "missing_effect")],
        )
        run2 = await _add_run(
            s,
            p1,
            NOW - timedelta(days=1),
            [
                _finding("seq#a", "spec:orders", "VERIFIED_IN_SCOPE", "conformant"),
                _finding(
                    "seq#b",
                    "spec:orders",
                    "UNKNOWN",
                    message="図の要素に対応する操作が見つからない: pay",
                ),
                _finding(
                    "sm#c",
                    "spec:orders",
                    "UNKNOWN",
                    message="経路 p1 は解析の精度が partial のため判定しない（while）",
                ),
                _finding(
                    "seq#d",
                    "spec:orders",
                    "UNKNOWN",
                    "missing_finding",
                    "要求 seq#d の判定結果が出ていない",
                ),
                _finding(
                    "MAKE-001:Makefile", "policy:make.targets", "REVIEW", "potential_violation"
                ),
                _finding(
                    "LLM-001:src/a.py",
                    "policy:llm.checklist",
                    "REVIEW",
                    "potential_violation",
                    outcome="split",
                ),
                _finding(
                    "LLM-001:src/b.py",
                    "policy:llm.checklist",
                    "REVIEW",
                    "potential_violation",
                    outcome="issue",
                ),
                _finding(LOCK_REQUIREMENT_ID, "knowledge:lock", "UNKNOWN", message="lock が無い"),
            ],
        )
        # 実行中の run は最新の完了した run にも推移にも入らない
        await _add_run(s, p1, NOW, [], state="running", gate=None)

        # 取得: ci（P1、最後は世代 2）、laptop（P2、世代 1）、new（未取得）、失効したトークン
        def token(name: str, project: uuid.UUID, revoked: bool = False) -> SyncTokenRecord:
            return SyncTokenRecord(
                team=ws,
                project_id=project,
                name=name,
                token_hash=uuid.uuid4().hex,
                created_by="owner1",
                revoked_at=NOW if revoked else None,
            )

        ci, laptop, new, gone, old = (
            token("ci", p1),
            token("laptop", p2),
            token("new", p1),
            token("gone", p1, True),
            token("old", p2, True),
        )
        mcp = McpTokenRecord(team=ws, name="agent", token_hash=uuid.uuid4().hex, created_by="o")
        mcp_old = McpTokenRecord(
            team=ws, name="retired", token_hash=uuid.uuid4().hex, created_by="o", revoked_at=NOW
        )
        s.add_all([ci, laptop, new, gone, old, mcp, mcp_old])
        await s.flush()

        def fetch(
            channel: str,
            generation: int,
            ago: timedelta,
            project: uuid.UUID | None,
            token_id: uuid.UUID | None = None,
            uid: str | None = None,
        ) -> KnowledgeFetchRecord:
            return KnowledgeFetchRecord(
                workspace=ws,
                channel=channel,
                token_id=token_id,
                uid=uid,
                bundle_generation=generation,
                project_id=project,
                artifact_ids=[],
                created_at=NOW - ago,
            )

        s.add_all(
            [
                fetch("sync", 1, timedelta(days=2), p1, ci.id),
                fetch("sync", 2, timedelta(days=1), p1, ci.id),
                fetch("sync", 1, timedelta(days=3), p2, laptop.id),
                fetch("mcp", 1, timedelta(days=40), p1, mcp.id, "member1"),  # 期間外
                fetch("mcp", 2, timedelta(days=5), p1, None, "member1"),
                # 失効したトークンの取得履歴は出すが、遅れには数えない
                fetch("sync", 1, timedelta(days=4), p2, old.id),
                fetch("mcp", 1, timedelta(days=4), p2, mcp_old.id),
            ]
        )
    return World(factory, ws, other, p1, p2, run1, run2)


async def test_workspace_health_numbers(world: World) -> None:
    async with world.factory() as s:
        h = await workspace_health(s, world.ws, now=NOW)

    assert h.current_generation == 2
    by_repo = {p.repo_full_name: p for p in h.projects}
    assert set(by_repo) == {f"e2e/{world.ws}-a", f"e2e/{world.ws}-b"}

    a = by_repo[f"e2e/{world.ws}-a"]
    assert a.formalization is not None
    assert (a.formalization.numerator, a.formalization.denominator) == (2, 4)
    assert a.binding is not None
    assert (a.binding.numerator, a.binding.denominator) == (3, 4)
    assert a.unknown_breakdown is not None
    assert a.unknown_breakdown.model_dump() == {
        "unbound": 1,
        "inexact_path": 1,
        "analysis_limit": 0,
        "missing_finding": 1,
        "lock": 1,
        "other": 0,
    }
    assert a.latest_run is not None
    assert a.latest_run.run_id == str(world.run2)
    assert a.latest_run.gate_status == "fail"
    assert a.latest_run.counts.model_dump() == {
        "VERIFIED_IN_SCOPE": 1,
        "VIOLATED": 0,
        "UNKNOWN": 4,
        "REVIEW": 3,
    }
    assert (a.latest_run.bundle_generation, a.latest_run.generation_lag) == (1, 1)

    # 要求の表が空・run が無い → 0% ではなく未計測（None）
    b = by_repo[f"e2e/{world.ws}-b"]
    assert b.formalization is None and b.binding is None
    assert b.unknown_breakdown is None and b.latest_run is None


async def test_fetch_health(world: World) -> None:
    async with world.factory() as s:
        h = await workspace_health(s, world.ws, now=NOW)

    rows = {(f.channel, f.token_name or f.uid): f for f in h.fetches}
    assert set(rows) == {
        ("sync", "ci"),
        ("sync", "laptop"),
        ("sync", "new"),
        ("sync", "old"),
        ("mcp", "retired"),
        ("mcp", "member1"),
    }
    for key in (("sync", "old"), ("mcp", "retired")):
        assert rows[key].revoked and rows[key].behind_by is None
        assert rows[key].last_generation == 1
    assert not rows[("sync", "ci")].revoked
    ci = rows[("sync", "ci")]
    assert (ci.last_generation, ci.behind_by) == (2, 0)
    assert ci.repo_full_name == f"e2e/{world.ws}-a"
    laptop = rows[("sync", "laptop")]
    assert (laptop.last_generation, laptop.behind_by) == (1, 1)
    new = rows[("sync", "new")]
    assert new.last_fetched_at is None and new.behind_by is None
    member = rows[("mcp", "member1")]
    assert member.member_email == "m1@example.com" and member.behind_by == 0


async def test_rule_trend_and_llm(world: World) -> None:
    async with world.factory() as s:
        h = await workspace_health(s, world.ws, now=NOW)

    assert [r.run_id for r in h.trend_runs] == [str(world.run1), str(world.run2)]
    rules = {r.rule_id: r for r in h.rules}
    assert set(rules) == {"DOC-001", "LLM-001", "MAKE-001"}
    make = rules["MAKE-001"]
    assert (make.level, make.tier, make.title) == ("mandatory", "deterministic", "MAKE-001 の題")
    assert make.points == [RulePoint(violated=1), RulePoint(review=1)]
    llm_rule = rules["LLM-001"]
    assert llm_rule.tier == "llm_assisted"
    assert llm_rule.points == [None, RulePoint(review=2)]
    assert rules["DOC-001"].tier == "unverified" and rules["DOC-001"].points == [None, None]

    assert h.llm is not None
    assert (h.llm.issue, h.llm.no_issue, h.llm.split) == (1, 0, 1)
    assert h.llm.cache_hit_rate is None and h.llm.attestations is None


async def test_llm_stats_from_optional_source(world: World) -> None:
    async def source(session: AsyncSession, workspace: str) -> LlmUsageStats | None:
        assert workspace == world.ws
        return LlmUsageStats(cache_hit_rate=0.75, attestations=2)

    async with world.factory() as s:
        h = await workspace_health(s, world.ws, now=NOW, llm_stats=source)
    assert h.llm is not None
    assert (h.llm.cache_hit_rate, h.llm.attestations) == (0.75, 2)


async def test_other_workspace_never_counted(world: World) -> None:
    async with world.factory() as s:
        h = await workspace_health(s, world.ws, now=NOW)
        o = await workspace_health(s, world.other, now=NOW)

    assert all(world.other not in p.repo_full_name for p in h.projects)
    assert all(f.uid != "owner9" for f in h.fetches)
    assert h.current_generation == 2
    # 他のワークスペースの MAKE-001 の違反（世代 9 の run）は推移に入らない
    assert len(h.trend_runs) == 2
    # 逆側からも、こちらのデータは見えない
    assert o.current_generation == 9
    assert [p.repo_full_name for p in o.projects] == [f"e2e/{world.other}"]
    assert [f.uid for f in o.fetches] == ["owner9"]
    assert {r.rule_id for r in o.rules} == {"MAKE-001"}
    assert o.llm is None


async def test_empty_workspace_is_unmeasured(engine: AsyncEngine) -> None:
    ws = f"e2e-obe-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{ws}", {"owner1": "owner"}, ws)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        h = await workspace_health(s, ws, now=NOW)
    assert h.current_generation is None
    [p] = h.projects
    assert p.formalization is None and p.binding is None
    assert p.unknown_breakdown is None and p.latest_run is None
    assert h.fetches == [] and h.trend_runs == [] and h.rules == [] and h.llm is None


# ---- REST ----------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host = LocalCodeHost(repos={})
    queue = InProcessQueue(lambda rid: None, immediate=False)  # type: ignore[arg-type,return-value]
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_api_member_sees_health(world: World, client: httpx.AsyncClient) -> None:
    r = await client.get(
        f"/api/workspaces/{world.ws}/observability", headers={"X-Dev-User": "member1"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["workspace"] == world.ws and body["current_generation"] == 2
    a = next(p for p in body["projects"] if p["repo_full_name"] == f"e2e/{world.ws}-a")
    assert a["formalization"]["numerator"] == 2 and a["formalization"]["denominator"] == 4
    b = next(p for p in body["projects"] if p["repo_full_name"] == f"e2e/{world.ws}-b")
    assert b["formalization"] is None and b["binding"] is None


async def test_api_hides_other_workspace(world: World, client: httpx.AsyncClient) -> None:
    # 別のワークスペースの owner・存在しないワークスペースは 404（存在を漏らさない）
    r = await client.get(
        f"/api/workspaces/{world.ws}/observability", headers={"X-Dev-User": "owner9"}
    )
    assert r.status_code == 404
    r = await client.get("/api/workspaces/e2e-none/observability", headers={"X-Dev-User": "owner9"})
    assert r.status_code == 404
