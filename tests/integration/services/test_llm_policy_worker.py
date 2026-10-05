"""S70: worker が LLM 支援の規約を評価し、記録・確定を使う（ReviewRunService × DB）。

実 PostgreSQL ＋ ローカル git。LLM は偽物。`SPECLINO_DATABASE_URL` が未設定なら skip。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.db.repositories import FindingRepository, ReviewRunRepository
from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRevision
from speclino.services.attestations import attest, attestation_target
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo
from tests.unit.services.test_llm_gate import RULE_ID, _llm_policy
from tests.unit.services.test_llm_policies import FakeLlm, _answer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


async def test_worker_assesses_records_and_applies_attestations(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    slug = f"e2e-llm-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = await project_id_of(engine, repo)
    f = async_sessionmaker(engine, expire_on_commit=False)
    policy = _llm_policy().model_copy(update={"team": slug})

    async def resolve_team_policy(team: str) -> PolicyRevision | None:
        assert team == slug
        return policy

    llm = FakeLlm([_answer(True, "def restore", item="intent")])
    llm.model = f"fake-{slug}"  # 他のテストの記録と混ざらないモデル名
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    holder: dict[str, ReviewRunService] = {}
    svc = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True),
        blobs=FileSystemBlobStore(tmp_path / "blobs"),
        config=ReviewRunConfig(
            agent_enabled=False, resolve_team_policy=resolve_team_policy, policy_llm=llm
        ),
    )
    holder["svc"] = svc

    async def run_once() -> tuple[dict[str, Finding], dict[str, object], str | None]:
        run_id = await svc.create_run(
            project_id=pid, head_sha=head, base_sha=base, pr_number=1, trigger="pull_request"
        )
        async with f() as s:
            run = await ReviewRunRepository(s).get(run_id)
            assert run is not None and run.exec_state == "completed", run
            found = {
                x.payload["id"]: Finding.model_validate(x.payload)
                for x in await FindingRepository(s).list_for_run(run_id)
                if x.requirement_id.startswith(RULE_ID)
            }
        assert run.manifest is not None
        return found, run.manifest, run.gate_status

    first, manifest, _ = await run_once()
    assert llm.calls == 3  # inventory.restore の 1 単位 × 3 回
    (finding,) = first.values()
    assert (finding.verdict, finding.assessment_outcome) == ("REVIEW", "issue")
    keys = manifest["llm_assessment_keys"]
    assert isinstance(keys, list) and len(keys) == 1

    # 同じ内容 → 記録を使い、LLM を呼ばない。結果も同じ
    second, manifest2, _ = await run_once()
    assert llm.calls == 3
    assert second.keys() == first.keys() and manifest2["llm_assessment_keys"] == keys

    # 確定すると、次の run でその指紋の REVIEW は阻止から外れ、manifest に残る
    target = attestation_target(finding)
    assert target is not None
    async with f() as s, s.begin():
        await attest(
            s,
            fingerprint=target[0],
            content_hash=target[1],
            disposition="attested",
            actor="owner1",
            workspace=slug,
        )
    third, manifest3, _ = await run_once()
    applied = manifest3["attestations"]
    assert isinstance(applied, list)
    assert [(a["fingerprint"], a["disposition"]) for a in applied] == [(target[0], "attested")]
    assert next(iter(third.values())).verdict == "REVIEW"  # verdict は変えない


async def test_worker_without_llm_leaves_the_rule_unknown(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    slug = f"e2e-nollm-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = await project_id_of(engine, repo)
    f = async_sessionmaker(engine, expire_on_commit=False)
    policy = _llm_policy().model_copy(update={"team": slug})

    async def resolve_team_policy(team: str) -> PolicyRevision | None:
        return policy

    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    holder: dict[str, ReviewRunService] = {}
    svc = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True),
        blobs=FileSystemBlobStore(tmp_path / "blobs"),
        config=ReviewRunConfig(agent_enabled=False, resolve_team_policy=resolve_team_policy),
    )
    holder["svc"] = svc
    run_id = await svc.create_run(
        project_id=pid, head_sha=head, base_sha=base, pr_number=1, trigger="pull_request"
    )
    async with f() as s:
        run = await ReviewRunRepository(s).get(run_id)
        found = [
            x
            for x in await FindingRepository(s).list_for_run(run_id)
            if x.requirement_id.startswith(RULE_ID)
        ]
    assert run is not None and run.gate_status == "fail"
    assert [x.verdict for x in found] == ["UNKNOWN"]


async def test_same_code_in_two_workspaces_is_assessed_separately(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """記録はワークスペースごと。別のワークスペースの評価でゲートが決まらない。"""
    tag = uuid.uuid4().hex[:8]
    slugs = [f"e2e-llmw{n}-{tag}" for n in "ab"]
    repos = [f"e2e/{s}-orders" for s in slugs]
    for slug, repo in zip(slugs, repos, strict=True):
        await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    f = async_sessionmaker(engine, expire_on_commit=False)

    async def resolve_team_policy(team: str) -> PolicyRevision | None:
        # 同じ改訂番号・同じ規約（出所も team 名以外は同じ）。記録の区切りはワークスペース
        return _llm_policy().model_copy(update={"team": team})

    llm = FakeLlm([_answer(True, "def restore", item="intent")])
    llm.model = f"fake-{tag}"
    base, head, _ = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(
        repos={r: tmp_path / "repo" for r in repos}, repo_ids={r: repo_id_of(r) for r in repos}
    )
    holder: dict[str, ReviewRunService] = {}
    svc = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True),
        blobs=FileSystemBlobStore(tmp_path / "blobs"),
        config=ReviewRunConfig(
            agent_enabled=False, resolve_team_policy=resolve_team_policy, policy_llm=llm
        ),
    )
    holder["svc"] = svc
    for n, repo in enumerate(repos, start=1):
        await svc.create_run(
            project_id=await project_id_of(engine, repo),
            head_sha=head,
            base_sha=base,
            pr_number=1,
            trigger="pull_request",
        )
        assert llm.calls == 3 * n
    async with engine.connect() as conn:
        rows = await conn.execute(
            sql_text("SELECT workspace FROM llm_assessments WHERE key LIKE :m ORDER BY workspace"),
            {"m": f"%fake-{tag}%"},
        )
        assert [r[0] for r in rows] == sorted(slugs)
