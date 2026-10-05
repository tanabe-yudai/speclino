"""S65: bundle を固定した run の replay（services/replay_service.replay_bundle）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from speclino.cli import app as cli_app
from speclino.services import knowledge_registry as kr
from speclino.services import replay_service
from speclino.services.check_pipeline import CheckRequest, run_check, write_result
from speclino.services.policy_governance import seed_from_package
from speclino.services.spec_import import import_specs
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.unit.services.test_check_pipeline_bundle import NOW, _policy, make_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


@dataclass
class Recorded:
    f: async_sessionmaker[AsyncSession]
    slug: str
    pid: str
    head: Path
    out: Path


async def _record(engine: AsyncEngine, tmp_path: Path) -> Recorded:
    """examples/orders の仕様とチーム規約（MAKE-001）を承認した bundle で run を記録する。"""
    slug = f"e2e-rp-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = str(await project_id_of(engine, repo))
    f = async_sessionmaker(engine, expire_on_commit=False)
    head = make_repo(tmp_path / "head")
    async with f() as s, s.begin():
        await seed_from_package(s, team=slug, package=_policy(on_missing="violation").package)
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
    async with f() as s, s.begin():
        bundle = await kr.ensure_bundle(s, slug)
        knowledge = await kr.resolve_for_project(s, bundle, pid)
    result = run_check(
        CheckRequest(
            repo_root=head,
            head_sha="WORKTREE",
            run_id="run-rp",
            knowledge=knowledge,
            github_repo_id=repo_id_of(repo),
            now=NOW,
            today=NOW.date(),
            trusted_root=head,
            trusted_sha="default-tip",
        )
    )
    assert any(x.requirement_id.startswith("MAKE-001") for x in result.findings.findings)
    out = tmp_path / "out"
    write_result(result, out)
    return Recorded(f, slug, pid, head, out)


async def _replay(r: Recorded, pid: str | None = None) -> replay_service.BundleReplayResult:
    async with r.f() as s:
        return await replay_service.replay_bundle(
            s, r.out, project_id=pid or r.pid, repo_root=r.head, trusted_root=r.head
        )


async def test_replay_reproduces_the_bundle_run(engine: AsyncEngine, tmp_path: Path) -> None:
    r = await _record(engine, tmp_path)
    res = await _replay(r)
    assert res.identical, res.differences
    assert res.compared > 0 and res.replayed is not None
    ids = {x.requirement_id for x in res.replayed.findings}
    assert {"R-CANCEL-04", "KNOWLEDGE-LOCK-001"} <= ids
    # 旧 replay は bundle の run を受け付けない
    with pytest.raises(ValueError, match="replay_bundle"):
        replay_service.replay(r.out, require_policy_check=False)


async def test_replay_reruns_policy_verifiers(engine: AsyncEngine, tmp_path: Path) -> None:
    """保存された規約の Finding も再評価する（書き換えれば不一致になる）。"""
    r = await _record(engine, tmp_path)
    path = r.out / "findings.json"
    data = json.loads(path.read_text())
    for x in data["findings"]:
        if x["requirement_id"].startswith("MAKE-001"):
            x["verdict"], x["category"] = "VERIFIED_IN_SCOPE", "conformant"
    path.write_text(json.dumps(data, ensure_ascii=False))
    res = await _replay(r)
    assert not res.identical
    assert any(d.startswith("MAKE-001") for d in res.differences), res.differences


async def test_replay_rejects_a_bundle_that_does_not_match(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    r = await _record(engine, tmp_path)
    manifest_path = r.out / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["bundle_hash"] = "sha256:" + "f" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="ハッシュ"):
        await _replay(r)


async def test_replay_only_resolves_the_projects_workspace(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """別のワークスペースのプロジェクトを指定しても、そのワークスペースの bundle しか引かない。"""
    r = await _record(engine, tmp_path)
    other = f"e2e-rpo-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{other}-x", {"owner1": "owner"}, team=other)
    other_pid = str(await project_id_of(engine, f"e2e/{other}-x"))
    with pytest.raises((LookupError, ValueError)):
        await _replay(r, other_pid)


async def test_cli_replay_bundle(engine: AsyncEngine, tmp_path: Path) -> None:
    r = await _record(engine, tmp_path)
    args = ["replay", str(r.out), "--project-id", r.pid, "--repo", str(r.head)]
    missing = await asyncio.to_thread(CliRunner().invoke, cli_app, args)
    assert missing.exit_code == 2 and "信頼側" in missing.output
    args += ["--trusted", str(r.head)]
    result = await asyncio.to_thread(CliRunner().invoke, cli_app, args)
    assert result.exit_code == 0, result.output
    assert "一致" in result.output and "bundle:" in result.output


async def test_replay_prefers_deterministic_findings(engine: AsyncEngine, tmp_path: Path) -> None:
    """エージェントの調査後の findings.json ではなく、調査前の決定的な Findings と比べる。"""
    r = await _record(engine, tmp_path)
    (r.out / replay_service.DETERMINISTIC_FINDINGS).write_text(
        (r.out / "findings.json").read_text()
    )
    data = json.loads((r.out / "findings.json").read_text())
    for x in data["findings"]:
        if x["requirement_id"] == "R-CANCEL-04":
            x["verdict"], x["category"] = "UNKNOWN", "unsupported_analysis"  # 調査で変わった値
    (r.out / "findings.json").write_text(json.dumps(data, ensure_ascii=False))
    assert (await _replay(r)).identical
    manifest = json.loads((r.out / "run-manifest.json").read_text())
    assert (
        manifest["trusted_commit"] == "default-tip" and manifest["today"] == NOW.date().isoformat()
    )
