"""S43: 下書きの upsert・承認の番号確認・拒否の監査・一覧（services/policy_governance.py）。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.policy import PolicyApplicability, PolicyPackage, PolicyRule
from speclino.services import policy_governance as gov

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


def _rule(rid: str = "DOC-001", level: str = "advisory", verifier: str | None = None) -> PolicyRule:
    """verifier は検証器 ID（登録済みなら版を引き、未登録なら版 "1"）。"""
    return PolicyRule.model_validate(
        {
            "id": rid,
            "version": "1.0.0",
            "level": level,
            "title": f"{rid} の題",
            "text": "本文",
            "applies_to": PolicyApplicability(paths=["docs/**"]).model_dump(),
            "verifier": {"id": verifier, "version": gov.KNOWN_VERIFIERS.get(verifier, "1")}
            if verifier
            else None,
            "on_missing_input": "unknown",
        }
    )


def _pkg(*rules: PolicyRule) -> PolicyPackage:
    return PolicyPackage(id="team", version="1", status="active", owner="o", rules=list(rules))


async def _workspace(engine: AsyncEngine) -> str:
    slug = f"t-pol-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO teams (team, approvers) VALUES (:t, '[]')"), {"t": slug})
        for uid, role in (("owner1", "owner"), ("member1", "member")):
            await c.execute(
                text("INSERT INTO workspace_members (team, uid, role) VALUES (:t, :u, :r)"),
                {"t": slug, "u": uid, "r": role},
            )
    return slug


async def test_save_draft_creates_then_updates_the_same_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        first = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="追加",
            expected_revision=None,
            expected_hash=None,
        )
    async with f() as s, s.begin():
        second = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule(level="mandatory", verifier="spec_tests")),
            author="owner1",
            note="必須に",
            expected_revision=first.revision,
            expected_hash=gov.draft_hash(first),
        )
    assert second.revision == first.revision and second.status == "draft"
    assert second.package.rules[0].level == "mandatory"
    async with f() as s:
        drafts = [
            r for r in await PolicyRevisionRepository(s).list_revisions(slug) if r.status == "draft"
        ]
        assert len(drafts) == 1
        audit = await AuditRepository(s).list_for_team(slug, ("policy.draft",), 10)
        assert [a.decision for a in audit] == ["updated", "created"]


async def test_invalid_verifier_and_duplicate_ids_are_rejected(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    for pkg in (_pkg(_rule(verifier="nope")), _pkg(_rule(), _rule())):
        with pytest.raises(gov.InvalidPackage):
            async with f() as s, s.begin():
                await gov.save_draft(
                    s,
                    team=slug,
                    package=pkg,
                    author="member1",
                    note="",
                    expected_revision=None,
                    expected_hash=None,
                )


async def test_non_approver_is_denied_and_the_denial_is_audited(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
    with pytest.raises(PermissionError):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision + 9,  # 番号が違っても、承認者でなければ 403 が先
                expected_hash="sha256:stale",
                approver="member1",
                audit_factory=f,
            )
    async with f() as s:
        assert (await PolicyRevisionRepository(s).current_draft(slug)) is not None
        denied = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
        assert [(a.decision, a.target) for a in denied] == [("denied", f"{slug}@{draft.revision}")]


async def test_approve_checks_the_revision_the_approver_saw(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
    with pytest.raises(gov.DraftConflict):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision + 1,
                expected_hash=gov.draft_hash(draft),
                approver="owner1",
                audit_factory=f,
            )
    # 承認者が見た後で別の人が同じ下書きの中身を変えた → 見たハッシュとずれるので拒否
    seen = gov.draft_hash(draft)
    async with f() as s, s.begin():
        changed = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule(), _rule("DOC-002")),
            author="member1",
            note="追加",
            expected_revision=draft.revision,
            expected_hash=seen,
        )
    with pytest.raises(gov.DraftConflict):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision,
                expected_hash=seen,
                approver="owner1",
                audit_factory=f,
            )
    async with f() as s, s.begin():
        active = await gov.approve_draft(
            s,
            team=slug,
            expected_revision=changed.revision,
            expected_hash=gov.draft_hash(changed),
            approver="owner1",
            audit_factory=f,
        )
    assert active.status == "active"
    async with f() as s:
        assert await PolicyRevisionRepository(s).current_draft(slug) is None
        [summary] = await PolicyRevisionRepository(s).list_summaries(slug, 20)
        assert (summary.status, summary.rule_count, summary.approver) == ("active", 2, "owner1")
        assert summary.approved_at is not None


async def test_author_can_approve_their_own_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="owner1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
        active = await gov.approve_draft(
            s,
            team=slug,
            expected_revision=draft.revision,
            expected_hash=gov.draft_hash(draft),
            approver="owner1",
            audit_factory=f,
        )
    assert active.author == active.approver == "owner1"
    async with f() as s:
        [approved] = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
        assert approved.detail["self_approved"] is True
        both = await AuditRepository(s).list_for_team(slug, ("policy.draft", "policy.approve"), 10)
        # 同じトランザクション（同じ時刻）でも承認が作成より新しく並ぶ
        assert [a.action for a in both] == ["policy.approve", "policy.draft"]


async def test_create_draft_refuses_a_second_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        await gov.create_draft(s, team=slug, package=_pkg(_rule()), author="member1")
    with pytest.raises(gov.DraftExists):
        async with f() as s, s.begin():
            await gov.create_draft(s, team=slug, package=_pkg(_rule()), author="owner1")


async def test_audit_does_not_leak_a_workspace_whose_slug_shares_the_prefix(
    engine: AsyncEngine,
) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        await AuditRepository(s).record(
            actor="x",
            action="policy.draft",
            target=f"{slug}@other@1",
            decision="created",
            detail={},
        )
        await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
    async with f() as s:
        rows = await AuditRepository(s).list_for_team(slug, ("policy.draft",), 10)
        assert [r.target for r in rows] == [f"{slug}@1"]


async def test_save_draft_rejects_a_stale_base(engine: AsyncEngine) -> None:
    """古い画面から保存して、他の人の変更を黙って上書きしない。"""
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
    for rev, h in ((None, None), (draft.revision, "sha256:stale")):
        with pytest.raises(gov.DraftConflict):
            async with f() as s, s.begin():
                await gov.save_draft(
                    s,
                    team=slug,
                    package=_pkg(_rule("DOC-009")),
                    author="owner1",
                    note="",
                    expected_revision=rev,
                    expected_hash=h,
                )


async def test_self_approval_counts_anyone_who_edited_the_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule()),
            author="member1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
        edited = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule("DOC-002")),
            author="owner1",
            note="",
            expected_revision=draft.revision,
            expected_hash=gov.draft_hash(draft),
        )
        await gov.approve_draft(
            s,
            team=slug,
            expected_revision=edited.revision,
            expected_hash=gov.draft_hash(edited),
            approver="owner1",
            audit_factory=f,
        )
    async with f() as s:
        [approved] = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
        assert approved.detail["self_approved"] is True


async def test_verifier_version_must_match(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    rule = _rule(verifier="spec_tests")
    bad = rule.model_copy(
        update={"verifier": rule.verifier.model_copy(update={"version": "9.9.9"})}
    )  # type: ignore[union-attr]
    with pytest.raises(gov.InvalidPackage):
        async with f() as s, s.begin():
            await gov.save_draft(
                s,
                team=slug,
                package=_pkg(bad),
                author="member1",
                note="",
                expected_revision=None,
                expected_hash=None,
            )


async def test_broad_llm_rule_is_approved_with_a_warning(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """#114: LLM に送るパスが広すぎる規約も承認はできるが、画面の下書きと監査に警告を残す。"""
    from speclino.adapters.storage.fs import FileSystemBlobStore
    from speclino.services.team_policies import load_policies

    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    rule = PolicyRule.model_validate(
        {
            "id": "LLM-001",
            "version": "1",
            "level": "advisory",
            "title": "t",
            "text": "t",
            "applies_to": {"paths": ["**/*.py"]},
            "verifier": {
                "id": "llm.checklist",
                "version": gov.KNOWN_VERIFIERS["llm.checklist"],
                "params": {"items": [{"id": "i", "question": "q"}], "unit_kind": "function"},
            },
            "on_missing_input": "unknown",
            "tier": "llm_assisted",
        }
    )
    async with f() as s, s.begin():
        draft = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(rule),
            author="owner1",
            note="",
            expected_revision=None,
            expected_hash=None,
        )
    async with f() as s:
        view = await load_policies(s, FileSystemBlobStore(tmp_path), slug, "owner1")
    assert len(view.draft_warnings) == 2
    async with f() as s, s.begin():
        await gov.approve_draft(
            s,
            team=slug,
            expected_revision=draft.revision,
            expected_hash=gov.draft_hash(draft),
            approver="owner1",
            audit_factory=f,
        )
    async with f() as s:
        [approved] = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
    assert approved.detail["warnings"] == view.draft_warnings
