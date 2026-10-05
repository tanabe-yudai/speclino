"""S70: replay は LLM 支援の規約を記録だけで再評価する（LLM を呼ばない）。実 PostgreSQL が必要。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories.assessments import AssessmentRepository
from speclino.services import knowledge_registry as kr
from speclino.services import replay_service
from speclino.services.check_pipeline import (
    CheckRequest,
    llm_assisted_rules,
    run_check,
    write_result,
)
from speclino.services.llm_policies import assess, checklist_params
from speclino.services.llm_units import extract_units
from speclino.services.policy_governance import seed_from_package
from speclino.services.spec_import import import_specs
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.unit.services.test_check_pipeline_bundle import NOW, _policy, make_repo
from tests.unit.services.test_llm_gate import RULE_ID, _llm_policy
from tests.unit.services.test_llm_policies import FakeLlm, _answer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


async def test_replay_uses_recorded_output(engine: AsyncEngine, tmp_path: Path) -> None:
    slug = f"e2e-rpl-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = str(await project_id_of(engine, repo))
    f = async_sessionmaker(engine, expire_on_commit=False)
    head = make_repo(tmp_path / "head")
    package = _policy().package.model_copy(
        update={"rules": [*_policy().package.rules, *_llm_policy().package.rules]}
    )
    async with f() as s, s.begin():
        await seed_from_package(s, team=slug, package=package)
        created = await import_specs(
            s, workspace=slug, project_id=pid, repo_root=head, author="owner1"
        )
    async with f() as s:
        draft = await kr.current_draft(s, created[0].artifact_id)
    assert draft is not None
    async with f() as s, s.begin():
        await kr.approve(
            s,
            artifact_id=draft.artifact_id,
            expected_revision=1,
            expected_hash=draft.content_hash,
            approver="owner1",
        )
        bundle = await kr.ensure_bundle(s, slug)
        knowledge = await kr.resolve_for_project(s, bundle, pid)
    req = CheckRequest(
        repo_root=head,
        head_sha="WORKTREE",
        run_id="run-rpl",
        knowledge=knowledge,
        github_repo_id=repo_id_of(repo),
        now=NOW,
        today=NOW.date(),
        trusted_root=head,
        trusted_sha="default-tip",
    )
    # worker と同じ手順で評価し、記録を DB に残す
    llm = FakeLlm([_answer(True, "def restore", item="intent")])
    llm.model = f"fake-{slug}"
    (target,) = llm_assisted_rules(req)
    rule = target.rule
    units = extract_units(head, checklist_params(rule), rule.applies_to.paths)
    async with f() as s, s.begin():
        found, keys = await assess(
            rule,
            units.units,
            llm=llm,
            store=AssessmentRepository(s, slug),
            policy_revision=target.policy_revision,
            origin=target.origin,
            now=NOW,
        )
    req.llm_findings, req.llm_assessment_keys = {rule.id: found}, tuple(keys)
    result = run_check(req)
    assert any(x.assessment_outcome == "issue" for x in result.findings.findings)
    out = tmp_path / "out"
    write_result(result, out)
    calls = llm.calls

    async with f() as s:
        res = await replay_service.replay_bundle(
            s, out, project_id=pid, repo_root=head, trusted_root=head
        )
    assert res.identical, res.differences
    assert llm.calls == calls  # replay は LLM を呼ばない
    assert any(i.startswith("llm:") for i in res.llm_findings)
    assert res.replayed is not None
    replayed = [x for x in res.replayed.findings if x.requirement_id.startswith(RULE_ID)]
    assert [x.assessment_outcome for x in replayed] == ["issue"]

    # 記録が無ければ UNKNOWN にし、差異として報告する
    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM llm_assessments WHERE key = :k"), {"k": keys[0]})
    async with f() as s:
        res = await replay_service.replay_bundle(
            s, out, project_id=pid, repo_root=head, trusted_root=head
        )
    assert not res.identical
    assert any("記録が無い" in d for d in res.differences), res.differences
    assert res.replayed is not None
    assert [x.verdict for x in res.replayed.findings if x.requirement_id.startswith(RULE_ID)] == [
        "UNKNOWN"
    ]
