"""S65: 旧形式の仕様の取り込み（services/spec_import.py・`speclino import-specs`）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from speclino.cli import app as cli_app
from speclino.domain.knowledge import ResolvedKnowledge, SpecContent
from speclino.services import knowledge_registry as kr
from speclino.services.check_pipeline import CheckRequest, run_check
from speclino.services.spec_import import artifact_name, import_specs
from tests.integration.services.conftest import project_id_of, register_repo
from tests.unit.services.test_check_pipeline_bundle import NOW, make_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


async def _ws(engine: AsyncEngine) -> tuple[str, str]:
    slug = f"e2e-imp-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{slug}-a", {"owner1": "owner"}, team=slug)
    return slug, str(await project_id_of(engine, f"e2e/{slug}-a"))


async def _approve_all(f: async_sessionmaker[AsyncSession], artifact_ids: list[str]) -> kr.Bundle:  # type: ignore[name-defined]
    bundle = None
    for aid in artifact_ids:
        async with f() as s:
            draft = await kr.current_draft(s, aid)
        assert draft is not None
        async with f() as s, s.begin():
            bundle = await kr.approve(
                s,
                artifact_id=aid,
                expected_revision=draft.revision,
                expected_hash=draft.content_hash,
                approver="owner1",
            )
    assert bundle is not None
    return bundle


async def _resolve(f: async_sessionmaker[AsyncSession], slug: str, pid: str) -> ResolvedKnowledge:
    async with f() as s, s.begin():
        bundle = await kr.ensure_bundle(s, slug)
        return await kr.resolve_for_project(s, bundle, pid)


def test_artifact_name() -> None:
    assert artifact_name(Path("specs/SPEC-TOOL-001.md")) == "spec-tool-001"
    assert artifact_name(Path("specs/orders-cancel.md")) == "orders-cancel"
    with pytest.raises(ValueError):
        artifact_name(Path("specs/日本語.md"))


async def test_import_creates_scoped_drafts(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, pid = await _ws(engine)
    root = make_repo(tmp_path / "repo")
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        created = await import_specs(
            s, workspace=slug, project_id=pid, repo_root=root, author="owner1"
        )
    assert [r.name for r in created] == ["orders-cancel"]
    rev = created[0]
    assert rev.status == "draft" and isinstance(rev.content, SpecContent)
    assert rev.content.scope.projects == [pid]
    assert [s.behavior_id for s in rev.content.legacy_ir] == ["cancel_order"]
    assert rev.content.domain is not None and "order.status" in rev.content.domain.variables
    assert rev.content.markdown == (root / "specs" / "orders-cancel.md").read_text()
    # 同じ名前の仕様をもう一度取り込むことはできない
    with pytest.raises(kr.ArtifactExists):
        async with f() as s, s.begin():
            await import_specs(s, workspace=slug, project_id=pid, repo_root=root, author="owner1")


async def test_ir_without_markdown_is_rejected(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, pid = await _ws(engine)
    root = make_repo(tmp_path / "repo")
    (root / "specs" / "orders-cancel.md").unlink()
    f = async_sessionmaker(engine, expire_on_commit=False)
    with pytest.raises(ValueError, match="cancel_order.spec-ir.json"):
        async with f() as s, s.begin():
            await import_specs(s, workspace=slug, project_id=pid, repo_root=root, author="owner1")


async def test_legacy_spec_same_findings_as_before(engine: AsyncEngine, tmp_path: Path) -> None:
    """examples/orders を取り込んで承認した bundle の run は、Git の run と同じ Findings を出す。"""
    slug, pid = await _ws(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    for variant, admin_only in (("good", False), ("bad", True)):
        root = make_repo(tmp_path / variant, admin_only_restore=admin_only)
        before = run_check(
            CheckRequest(repo_root=root, head_sha="WORKTREE", source_roots=(".",), run_id="r")
        )
        if variant == "good":
            async with f() as s, s.begin():
                created = await import_specs(
                    s, workspace=slug, project_id=pid, repo_root=root, author="owner1"
                )
            await _approve_all(f, [r.artifact_id for r in created])
        knowledge = await _resolve(f, slug, pid)
        assert [r.name for r in knowledge.specs] == ["orders-cancel"]
        after = run_check(
            CheckRequest(
                repo_root=root,
                head_sha="WORKTREE",
                source_roots=(".",),
                run_id="r",
                knowledge=knowledge,
                now=NOW,
                trusted_root=root,
                trusted_sha="default-tip",
            )
        )
        spec_findings = [
            x for x in after.findings.findings if not x.behavior_id.startswith("knowledge:")
        ]
        for x, y in zip(spec_findings, before.findings.findings, strict=True):
            dx, dy = x.model_dump(), y.model_dump()
            diff = {k: (dx[k], dy[k]) for k in dx if dx[k] != dy[k]}
            assert not diff, (variant, x.requirement_id, diff)
        verdicts = {x.requirement_id: x.verdict for x in spec_findings}
        assert verdicts["R-CANCEL-04"] == ("VIOLATED" if admin_only else "VERIFIED_IN_SCOPE")
        rule_ids = [r.id for r in after.spec_irs[0].rules]
        assert after.manifest.expected_requirement_ids[: len(rule_ids)] == rule_ids


async def test_cli_import_specs(engine: AsyncEngine, tmp_path: Path) -> None:
    slug, pid = await _ws(engine)
    root = make_repo(tmp_path / "repo")
    args = ["import-specs", "--workspace", slug, "--project-id", pid, "--author", "owner1"]
    result = await asyncio.to_thread(CliRunner().invoke, cli_app, [*args, "--repo", str(root)])
    assert result.exit_code == 0, result.output
    assert "orders-cancel: 下書き 1 を作成した" in result.output
    again = await asyncio.to_thread(CliRunner().invoke, cli_app, [*args, "--repo", str(root)])
    assert again.exit_code == 2 and "拒否した" in again.output
    empty = tmp_path / "empty"
    shutil.copytree(root / ".speclino", empty / ".speclino")
    none = await asyncio.to_thread(CliRunner().invoke, cli_app, [*args, "--repo", str(empty)])
    assert none.exit_code == 2 and "取り込む仕様が無い" in none.output
