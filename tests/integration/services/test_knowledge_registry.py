"""S62: 知識の改訂・承認・bundle（services/knowledge_registry.py）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。ワークスペースは `e2e-` で始め、
conftest の後片付けに任せる。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.db.models import AuditEvent
from speclino.domain.knowledge import (
    ArtifactScope,
    Bundle,
    InstructionsContent,
    SkillContent,
    SpecContent,
)
from speclino.domain.uml import CompiledSpec, RequirementRow
from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr
from speclino.services.policy_governance import DraftConflict
from tests.integration.services.conftest import project_id_of, register_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]

H0 = "sha256:" + "0" * 64


@dataclass
class Ws:
    f: async_sessionmaker[AsyncSession]
    slug: str
    p1: str
    p2: str
    other_slug: str
    other_project: str


@pytest_asyncio.fixture
async def ws(engine: AsyncEngine) -> AsyncIterator[Ws]:
    slug = f"e2e-kr-{uuid.uuid4().hex[:8]}"
    other = f"e2e-kro-{uuid.uuid4().hex[:8]}"
    members = {"owner1": "owner", "appr1": "approver", "member1": "member"}
    await register_repo(engine, f"e2e/{slug}-a", members, team=slug)
    await register_repo(engine, f"e2e/{slug}-b", members, team=slug)
    await register_repo(engine, f"e2e/{other}-a", {"owner9": "owner"}, team=other)
    yield Ws(
        async_sessionmaker(engine, expire_on_commit=False),
        slug,
        str(await project_id_of(engine, f"e2e/{slug}-a")),
        str(await project_id_of(engine, f"e2e/{slug}-b")),
        other,
        str(await project_id_of(engine, f"e2e/{other}-a")),
    )


def _ws_scope() -> ArtifactScope:
    return ArtifactScope(workspace=True)


def _projects(*ids: str) -> ArtifactScope:
    return ArtifactScope(projects=list(ids))


def _instr(body: str = "本文", scope: ArtifactScope | None = None) -> InstructionsContent:
    return InstructionsContent(
        kind="instructions", target="agents_md", body=body, scope=scope or _ws_scope()
    )


def _skill(name: str, scope: ArtifactScope | None = None) -> SkillContent:
    md = f"---\nname: {name}\ndescription: d\n---\n本文\n"
    return SkillContent(kind="skill", skill_md=md, scope=scope or _ws_scope())


def _compiled(*req_ids: str) -> CompiledSpec:
    return CompiledSpec(
        parser_version="test",
        source_hash=H0,
        ir_hash=H0,
        requirements=[RequirementRow(id=r, level="mandatory", text="t") for r in req_ids],
    )


def _spec(scope: ArtifactScope, compiled: CompiledSpec | None) -> SpecContent:
    return SpecContent(kind="spec", markdown="# 仕様", scope=scope, compiled=compiled)


async def _create(w: Ws, kind: str, name: str, content: object, author: str = "member1") -> str:
    async with w.f() as s, s.begin():
        rev = await kr.create_draft(
            s,
            workspace=w.slug,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            content=content,  # type: ignore[arg-type]
            author=author,
        )
    return rev.artifact_id


async def _approve(w: Ws, artifact_id: str, approver: str = "owner1") -> Bundle:
    async with w.f() as s:
        draft = await kr.current_draft(s, artifact_id)
    assert draft is not None
    async with w.f() as s, s.begin():
        return await kr.approve(
            s,
            artifact_id=artifact_id,
            expected_revision=draft.revision,
            expected_hash=draft.content_hash,
            approver=approver,
            audit_factory=w.f,
        )


async def _audit(s: AsyncSession, slug: str) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(
        AuditEvent.target.startswith(f"{slug}/", autoescape=True),
        AuditEvent.action.like("knowledge.%"),
    )
    return list((await s.execute(stmt)).scalars())


def _payload(bundle: Bundle) -> bytes:
    return json.dumps(
        bundle.model_dump(mode="json", exclude={"signature"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


async def test_draft_update_approve_and_new_revision(ws: Ws) -> None:
    aid = await _create(ws, "instructions", "agents", _instr("v1"))
    async with ws.f() as s, s.begin():
        updated = await kr.update_draft(
            s, artifact_id=aid, expected_revision=1, content=_instr("v1.1"), author="appr1"
        )
    assert updated.revision == 1 and updated.status == "draft"
    with pytest.raises(DraftConflict):
        async with ws.f() as s, s.begin():
            await kr.update_draft(
                s, artifact_id=aid, expected_revision=2, content=_instr("x"), author="member1"
            )

    bundle = await _approve(ws, aid)
    assert bundle.generation == 1 and bundle.workspace == ws.slug
    assert [(m.artifact_id, m.revision) for m in bundle.members] == [(aid, 1)]
    assert bundle_signing.verify(_payload(bundle), bundle.signature)
    async with ws.f() as s:
        stored = await kr.current_bundle(s, ws.slug)
        assert stored == bundle
        assert await kr.get_bundle(s, ws.slug, 1) == bundle
        with pytest.raises(kr.BundleNotFound):
            await kr.get_bundle(s, ws.slug, 99)

    # active から次の下書きを始める（見た番号は今の active）
    async with ws.f() as s, s.begin():
        second = await kr.update_draft(
            s, artifact_id=aid, expected_revision=1, content=_instr("v2"), author="member1"
        )
    assert second.revision == 2 and second.status == "draft"
    bundle2 = await _approve(ws, aid)
    assert bundle2.generation == 2 and bundle2.members[0].revision == 2
    async with ws.f() as s:
        revs = await kr.list_revisions(s, aid)
    assert [(r.revision, r.status) for r in revs] == [(2, "active"), (1, "superseded")]


async def test_concurrent_approve_one_wins(ws: Ws) -> None:
    aid = await _create(ws, "instructions", "agents", _instr())
    async with ws.f() as s:
        draft = await kr.current_draft(s, aid)
    assert draft is not None

    async def go(approver: str) -> Bundle:
        async with ws.f() as s, s.begin():
            return await kr.approve(
                s,
                artifact_id=aid,
                expected_revision=draft.revision,
                expected_hash=draft.content_hash,
                approver=approver,
            )

    results = await asyncio.gather(go("owner1"), go("appr1"), return_exceptions=True)
    assert sum(isinstance(r, Bundle) for r in results) == 1, results
    assert sum(isinstance(r, DraftConflict) for r in results) == 1, results
    async with ws.f() as s:
        current = await kr.current_bundle(s, ws.slug)
    assert current is not None and current.generation == 1


async def test_self_approval_is_flagged(ws: Ws) -> None:
    own = await _create(ws, "instructions", "own", _instr(), author="owner1")
    other = await _create(ws, "skill", "other", _skill("other"), author="member1")
    await _approve(ws, own, approver="owner1")
    await _approve(ws, other, approver="appr1")
    async with ws.f() as s:
        (own_rev,) = await kr.list_revisions(s, own)
        (other_rev,) = await kr.list_revisions(s, other)
        audit = await _audit(s, ws.slug)
    assert own_rev.self_approved is True and own_rev.approver == "owner1"
    assert other_rev.self_approved is False and other_rev.approver == "appr1"
    approvals = {a.target: a.detail["self_approved"] for a in audit if a.decision == "approved"}
    assert approvals == {f"{ws.slug}/{own}@1": True, f"{ws.slug}/{other}@1": False}


async def test_editor_approval_counts_as_self_approval(ws: Ws) -> None:
    aid = await _create(ws, "instructions", "agents", _instr(), author="member1")
    async with ws.f() as s, s.begin():
        await kr.update_draft(
            s, artifact_id=aid, expected_revision=1, content=_instr("直した"), author="appr1"
        )
    await _approve(ws, aid, approver="appr1")
    async with ws.f() as s:
        (rev,) = await kr.list_revisions(s, aid)
    assert rev.self_approved is True


async def test_non_approver_is_denied_and_audited(ws: Ws) -> None:
    aid = await _create(ws, "instructions", "agents", _instr())
    with pytest.raises(PermissionError):
        await _approve(ws, aid, approver="member1")
    with pytest.raises(PermissionError):
        await _approve(ws, aid, approver="owner9")  # 別のワークスペースの owner
    async with ws.f() as s:
        audit = await _audit(s, ws.slug)
        assert await kr.current_bundle(s, ws.slug) is None
    denied = [(a.actor, a.decision) for a in audit if a.action == "knowledge.approve"]
    assert sorted(denied) == [("member1", "denied"), ("owner9", "denied")]


async def test_stale_hash_is_a_conflict(ws: Ws) -> None:
    aid = await _create(ws, "instructions", "agents", _instr())
    with pytest.raises(DraftConflict):
        async with ws.f() as s, s.begin():
            await kr.approve(
                s, artifact_id=aid, expected_revision=1, expected_hash=H0, approver="owner1"
            )


async def test_bundle_conflict_rejects_approval(ws: Ws) -> None:
    first = await _create(ws, "instructions", "agents", _instr())
    await _approve(ws, first)
    second = await _create(ws, "instructions", "agents-a", _instr(scope=_projects(ws.p1)))
    with pytest.raises(kr.BundleConflict, match="agents_md"):
        await _approve(ws, second)
    async with ws.f() as s:
        current = await kr.current_bundle(s, ws.slug)
        (rev,) = await kr.list_revisions(s, second)
    assert current is not None and current.generation == 1
    assert rev.status == "draft"

    # 違う target ならぶつからない
    claude = InstructionsContent(
        kind="instructions", target="claude_md", body="b", scope=_projects(ws.p1)
    )
    third = await _create(ws, "instructions", "claude", claude)
    assert (await _approve(ws, third)).generation == 2


async def test_skill_name_and_requirement_ids_conflict(ws: Ws) -> None:
    a = await _create(ws, "skill", "review-a", _skill("review", _projects(ws.p1)))
    await _approve(ws, a)
    b = await _create(ws, "skill", "review-b", _skill("review", _projects(ws.p2)))
    await _approve(ws, b)  # スコープが重ならなければ同名でよい
    c = await _create(ws, "skill", "review-c", _skill("review", _ws_scope()))
    with pytest.raises(kr.BundleConflict, match="review"):
        await _approve(ws, c)

    s1 = await _create(ws, "spec", "orders", _spec(_projects(ws.p1), _compiled("REQ-1")))
    await _approve(ws, s1)
    s2 = await _create(ws, "spec", "orders2", _spec(_projects(ws.p1, ws.p2), _compiled("REQ-1")))
    with pytest.raises(kr.BundleConflict, match="REQ-1"):
        await _approve(ws, s2)


async def test_scope_project_must_belong_to_workspace(ws: Ws) -> None:
    for bad in (ws.other_project, str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(kr.InvalidArtifact):
            await _create(ws, "instructions", "agents", _instr(scope=_projects(bad)))

    # 下書きの後でプロジェクトが別のワークスペースへ移ったら、承認（bundle を作る）で拒否する
    aid = await _create(ws, "instructions", "agents", _instr(scope=_projects(ws.p2)))
    async with ws.f() as s, s.begin():
        await s.execute(
            text("UPDATE projects SET team = :t WHERE id = CAST(:p AS uuid)"),
            {"t": ws.other_slug, "p": ws.p2},
        )
    with pytest.raises(kr.BundleConflict, match=ws.p2):
        await _approve(ws, aid)


async def test_spec_with_errors_cannot_be_approved(ws: Ws) -> None:
    aid = await _create(ws, "spec", "orders", _spec(_projects(ws.p1), None))
    with pytest.raises(kr.SpecNotApprovable):
        await _approve(ws, aid)
    async with ws.f() as s, s.begin():
        await kr.update_draft(
            s,
            artifact_id=aid,
            expected_revision=1,
            content=_spec(_projects(ws.p1), _compiled("REQ-1")),
            author="member1",
        )
    assert (await _approve(ws, aid)).generation == 1


async def test_create_rejects_duplicates_and_kind_mismatch(ws: Ws) -> None:
    await _create(ws, "instructions", "agents", _instr())
    with pytest.raises(kr.ArtifactExists):
        await _create(ws, "instructions", "agents", _instr())
    with pytest.raises(kr.InvalidArtifact):
        await _create(ws, "skill", "mismatch", _instr())


async def test_resolve_for_project(ws: Ws) -> None:
    instr = await _create(ws, "instructions", "agents", _instr())
    skill = await _create(ws, "skill", "only-a", _skill("only-a", _projects(ws.p1)))
    spec = await _create(ws, "spec", "orders", _spec(_projects(ws.p2), _compiled("REQ-1")))
    for aid in (instr, skill, spec):
        bundle = await _approve(ws, aid)
    async with ws.f() as s:
        ra = await kr.resolve_for_project(s, bundle, ws.p1)
        rb = await kr.resolve_for_project(s, bundle, ws.p2)
        with pytest.raises(LookupError):
            await kr.resolve_for_project(s, bundle, ws.other_project)
    assert [r.artifact_id for r in ra.instructions] == [instr]
    assert [r.artifact_id for r in ra.skills] == [skill] and ra.specs == []
    assert [r.artifact_id for r in rb.specs] == [spec] and rb.skills == []
    assert ra.policy is None and ra.bundle == bundle

    tampered = bundle.model_copy(update={"generation": bundle.generation + 1})
    async with ws.f() as s:
        with pytest.raises(ValueError, match="署名"):
            await kr.resolve_for_project(s, tampered, ws.p1)


async def test_sync_token_and_fetch_tables(ws: Ws) -> None:
    """S68 が使う表の最小限の往復（発行・検索・失効・取得の記録）。"""
    from speclino.db.repositories.sync_tokens import KnowledgeFetchRepository, SyncTokenRepository

    h = f"sha256:{uuid.uuid4().hex}"
    async with ws.f() as s, s.begin():
        tok = await SyncTokenRepository(s).create(
            team=ws.slug, project_id=uuid.UUID(ws.p1), name="ci", token_hash=h, created_by="owner1"
        )
        await SyncTokenRepository(s).touch(tok.id)
        await KnowledgeFetchRepository(s).record(
            workspace=ws.slug,
            channel="sync",
            bundle_generation=1,
            artifact_ids=[],
            token_id=tok.id,
            project_id=uuid.UUID(ws.p1),
        )
    async with ws.f() as s, s.begin():
        found = await SyncTokenRepository(s).find_active(h)
        assert found is not None and found.last_used_at is not None
        assert not await SyncTokenRepository(s).revoke(token_id=tok.id, team=ws.other_slug)
        assert await SyncTokenRepository(s).revoke(token_id=tok.id, team=ws.slug)
    async with ws.f() as s:
        assert await SyncTokenRepository(s).find_active(h) is None


async def test_policy_activation_creates_bundle(ws: Ws) -> None:
    from speclino.domain.policy import PolicyPackage, PolicyRule
    from speclino.services import policy_governance as gov
    from speclino.services.check_pipeline import policy_package_hash

    aid = await _create(ws, "instructions", "agents", _instr())
    await _approve(ws, aid)
    rule = PolicyRule.model_validate(
        {
            "id": "DOC-001",
            "version": "1.0.0",
            "level": "advisory",
            "title": "題",
            "text": "本文",
            "applies_to": {"paths": ["docs/**"]},
            "verifier": None,
            "on_missing_input": "unknown",
        }
    )
    pkg = PolicyPackage(id="team", version="1", status="active", owner="o", rules=[rule])
    async with ws.f() as s, s.begin():
        draft = await gov.create_draft(s, team=ws.slug, package=pkg, author="member1")
        await gov.approve(s, team=ws.slug, revision=draft.revision, approver="owner1")
    async with ws.f() as s:
        bundle = await kr.current_bundle(s, ws.slug)
    assert bundle is not None and bundle.generation == 2
    assert bundle.policy_revision == draft.revision
    assert bundle.policy_hash == policy_package_hash(pkg)
    assert [m.artifact_id for m in bundle.members] == [aid]  # 承認済みの知識はそのまま残る
    async with ws.f() as s:
        resolved = await kr.resolve_for_project(s, bundle, ws.p1)
    assert resolved.policy is not None and resolved.policy.revision == draft.revision
