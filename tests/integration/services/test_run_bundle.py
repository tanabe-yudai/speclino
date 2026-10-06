"""S65: worker が run の開始時に bundle を固定する（ReviewRunService × knowledge_inputs）。

実 PostgreSQL ＋ ローカル git（GitHub なし）。`SPECLINO_DATABASE_URL` が未設定なら skip。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.db.repositories import FindingRepository, ReviewRunRepository
from speclino.domain.knowledge import ArtifactScope, ResolvedKnowledge, SpecContent
from speclino.services import knowledge_registry as kr
from speclino.services.knowledge_inputs import make_knowledge_resolver
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.services.spec_import import import_specs
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo
from tests.unit.services.test_check_pipeline_bundle import DOMAIN, UML, make_repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


async def _approve(f: async_sessionmaker[AsyncSession], aid: str) -> int:
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
    return bundle.generation


async def test_run_pins_bundle(engine: AsyncEngine, tmp_path: Path) -> None:
    """run の途中で承認されて bundle が進んでも、その run は開始時の bundle で判定する。"""
    slug = f"e2e-pin-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = str(await project_id_of(engine, repo))
    f = async_sessionmaker(engine, expire_on_commit=False)

    # 旧形式の仕様（R-CANCEL-01〜07）を取り込んで承認する
    async with f() as s, s.begin():
        created = await import_specs(
            s,
            workspace=slug,
            project_id=pid,
            repo_root=make_repo(tmp_path / "src"),
            author="owner1",
        )
    pinned = await _approve(f, created[0].artifact_id)

    real = make_knowledge_resolver(f)
    calls: list[int] = []

    async def resolve_then_approve(workspace: str, project_id: str) -> ResolvedKnowledge:
        k = await real(workspace, project_id)
        calls.append(k.bundle.generation)
        # 解決の直後（解析の前）に別の仕様が承認され、bundle が進む
        async with f() as s, s.begin():
            rev = await kr.create_draft(
                s,
                workspace=workspace,
                kind="spec",
                name="late-uml",
                content=SpecContent(
                    kind="spec", markdown=UML, scope=ArtifactScope(projects=[project_id])
                ),
                author="owner1",
            )
        await _approve(f, rev.artifact_id)
        return k

    base, _head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    holder: dict[str, ReviewRunService] = {}
    svc = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True),
        blobs=FileSystemBlobStore(tmp_path / "blobs"),
        config=ReviewRunConfig(agent_enabled=False, resolve_knowledge=resolve_then_approve),
    )
    holder["svc"] = svc
    # head2 は Git の IR から R-CANCEL-04 を消した PR。仕様は bundle にあるので消せない
    run_id = await svc.create_run(
        project_id=uuid.UUID(pid),
        head_sha=head2,
        base_sha=base,
        pr_number=1,
        trigger="pull_request",
    )

    async with f() as s:
        run = await ReviewRunRepository(s).get(run_id)
        assert run is not None and run.exec_state == "completed", run
        findings = {x.requirement_id: x for x in await FindingRepository(s).list_for_run(run_id)}
        current = await kr.current_bundle(s, slug)
    assert calls == [pinned]
    assert current is not None and current.generation == pinned + 1
    assert run.manifest is not None and run.manifest["bundle_generation"] == pinned
    # 後から承認された仕様（late-uml）の義務はこの run に出ない
    assert not any(r.startswith(("vocab#", "cancel#")) for r in findings)
    assert findings["R-CANCEL-04"].verdict == "VIOLATED"
    assert findings["KNOWLEDGE-LOCK-001"].verdict == "UNKNOWN"
    assert run.gate_status == "fail"


async def test_resolver_only_resolves_the_projects_own_workspace(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """別のワークスペースのプロジェクトには当てない。署名が合わない bundle は使わない。"""
    a, b = (f"e2e-rs{n}-{uuid.uuid4().hex[:6]}" for n in "ab")
    await register_repo(engine, f"e2e/{a}-x", {"owner1": "owner"}, team=a)
    await register_repo(engine, f"e2e/{b}-y", {"owner1": "owner"}, team=b)
    pid_b = str(await project_id_of(engine, f"e2e/{b}-y"))
    f = async_sessionmaker(engine, expire_on_commit=False)
    resolve = make_knowledge_resolver(f)
    with pytest.raises(LookupError):
        await resolve(a, pid_b)
    k = await resolve(b, pid_b)
    assert k.bundle.workspace == b

    tampered = k.bundle.model_copy(update={"generation": k.bundle.generation + 100})
    async with f() as s:
        with pytest.raises(ValueError, match="署名"):
            await kr.resolve_for_project(s, tampered, pid_b)


# ---------------------------------------------------------------- 信頼側（fix round 1）


class _TipHost(LocalCodeHost):
    """branch_head の答えを差し替えられる LocalCodeHost（None は取得失敗）。"""

    tip: str | None = None
    fail: bool = False

    async def branch_head(self, repo: str, branch: str) -> str | None:
        if self.fail:
            raise RuntimeError("GitHub に届かない")
        return self.tip if self.tip is not None else await super().branch_head(repo, branch)


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


async def _attack_setup(engine: AsyncEngine, tmp_path: Path) -> tuple[str, str, str, str, Path]:
    """main = 正しい実装、attack ブランチ = 呼出しを消して bindings で偽装した PR。"""
    from tests.unit.services.test_check_pipeline_bundle import _fake_call_head

    slug = f"e2e-tr-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}-orders"
    await register_repo(engine, repo, {"owner1": "owner"}, team=slug)
    pid = str(await project_id_of(engine, repo))
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        rev = await kr.create_draft(
            s,
            workspace=slug,
            kind="spec",
            name="orders-uml",
            content=SpecContent(
                kind="spec", markdown=UML, domain=DOMAIN, scope=ArtifactScope(projects=[pid])
            ),
            author="owner1",
        )
    await _approve(f, rev.artifact_id)
    root = make_repo(tmp_path / "repo")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "default")
    good = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "attack")
    _fake_call_head(root)
    _git(root, "commit", "-qam", "attack")
    attack = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "main")
    return repo, pid, good, attack, root


async def _run(
    engine: AsyncEngine, tmp_path: Path, host: LocalCodeHost, pid: str, **kw: object
) -> tuple[dict[str, object], dict[str, object]]:
    f = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    svc = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda run_id: holder["svc"].execute(run_id), immediate=True),
        blobs=FileSystemBlobStore(tmp_path / f"blobs-{uuid.uuid4().hex[:6]}"),
        config=ReviewRunConfig(agent_enabled=False, resolve_knowledge=make_knowledge_resolver(f)),
    )
    holder["svc"] = svc
    run_id = await svc.create_run(project_id=uuid.UUID(pid), **kw)  # type: ignore[arg-type]
    async with f() as s:
        run = await ReviewRunRepository(s).get(run_id)
        assert run is not None and run.exec_state == "completed", run
        rows = await FindingRepository(s).list_for_run(run_id)
    found = {x.requirement_id: x.verdict for x in rows}
    assert run.manifest is not None
    return dict(run.manifest), dict(found)


TRACE = "cancel#OrderService.cancel_order"


async def test_worker_trusts_only_the_default_branch_tip(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    repo, pid, good, attack, root = await _attack_setup(engine, tmp_path)
    host = _TipHost(repos={repo: root}, repo_ids={repo: repo_id_of(repo)})
    # (a) base_sha に PR の head 自身を渡しても、信頼側は main の先端
    manifest, found = await _run(
        engine,
        tmp_path,
        host,
        pid,
        head_sha=attack,
        base_sha=attack,
        pr_number=1,
        trigger="pull_request",
    )
    assert found[TRACE] == "VIOLATED" and found["KNOWLEDGE-BINDINGS-001"] == "REVIEW"
    assert manifest["trusted_commit"] == good
    # (b) check_suite の rerequest（base_sha なし）も同じ
    manifest, found = await _run(
        engine,
        tmp_path,
        host,
        pid,
        head_sha=attack,
        base_sha=None,
        pr_number=1,
        trigger="rerequested",
    )
    assert found[TRACE] == "VIOLATED" and found["KNOWLEDGE-BINDINGS-001"] == "REVIEW"
    # (c) code_host の先端が base_sha と違えば、先端の方を信頼側にする
    host.tip = good
    manifest, found = await _run(
        engine,
        tmp_path,
        host,
        pid,
        head_sha=attack,
        base_sha=attack,
        pr_number=2,
        trigger="pull_request",
    )
    assert manifest["trusted_commit"] == good and found[TRACE] == "VIOLATED"
    # (d) 先端を取れなければ対応を読まず、偽の成立にしない
    host.fail = True
    manifest, found = await _run(
        engine,
        tmp_path,
        host,
        pid,
        head_sha=attack,
        base_sha=attack,
        pr_number=3,
        trigger="pull_request",
    )
    assert manifest["trusted_commit"] is None
    assert found[TRACE] != "VERIFIED_IN_SCOPE" and found["KNOWLEDGE-BINDINGS-001"] == "REVIEW"
