"""ショップ題材を、ローカルの DB と git リポジトリに決定的に用意する。

`scripts/seed_demo.py` の本体。

やること（docs/demo-knowledge-registry.md の手順の前提）:

1. `repo_dir` に検査対象の git リポジトリを作る（main = 適合する実装、`pr/<変異>` = 変異ごとの
   PR）
2. ワークスペース・メンバー（owner = 画面の開発ログインの利用者、member = 仕様の作者）・
   プロジェクトを作る
3. 仕様（spec.md）・skill・AGENTS.md の指示を作者が下書きし、owner が承認する（2 人の承認）
4. LLM 支援の規約（チェックリスト）を owner が下書き・承認する
5. 承認後の bundle を main に sync し（署名つき lock）、main を検査する
6. main の LLM 支援の「指摘なし」を owner が確定し（必須の規約は指摘なしでも人の確定まで
   ゲートを止める。#114）、main を検査し直してから各 PR を検査する。確定は単位の内容に結びつく
   ので、PR で変わっていない単位にはそのまま効く

LLM は呼ばない。LLM 支援の規約は `ScriptedChecklistLlm`（コードの文字列だけで答える決定的な
偽物）で評価し、その記録を DB に残す。サーバーを同じモデル名（`SEED_LLM_MODEL`）で起動すると
記録を使い、LLM を呼ばない。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from examples.shop.materialize import DOMAIN, SPEC, apply_mutation, materialize, mutation_names
from speclino import sync_apply
from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.db.repositories import (
    FindingRepository,
    PolicyRevisionRepository,
    ProjectRepository,
)
from speclino.domain.finding import Finding
from speclino.domain.knowledge import (
    ArtifactContent,
    ArtifactKind,
    ArtifactScope,
    InstructionsContent,
    SkillContent,
    SpecContent,
)
from speclino.domain.policy import (
    PolicyApplicability,
    PolicyPackage,
    PolicyRevision,
    PolicyRule,
    PolicyVerifierRef,
)
from speclino.ports.text_llm import JsonGeneration
from speclino.services import knowledge_registry as kr
from speclino.services import knowledge_sync as ks
from speclino.services import policy_governance as pg
from speclino.services import workspace_admin as wa
from speclino.services.attestations import attest, attestation_target
from speclino.services.knowledge_inputs import make_knowledge_resolver
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from speclino.spec import load_domain

WORKSPACE = "shop-team"
REPO = "example/shop"
# 実在しえない GitHub のリポジトリ ID（JavaScript の安全な整数の上限から下に数える）。本物の
# リポジトリと取り違えないため
GITHUB_REPO_ID = 2**53 - 1
OWNER = "judge"  # web の開発ログイン（X-Dev-User）の利用者
AUTHOR = "spec-author"
SEED_LLM_MODEL = "scripted-checklist"
POLICY_RULE_ID = "SHOP-ERR-001"

SKILL_MD = """---
name: shop-error-format
description: ショップの OrderError のメッセージを書くときに使う。次に取る行動が分かる文にする。
---

# OrderError のメッセージの書式

- 1 文で、何が受け付けられなかったかと、利用者が次に取る行動を書く
- 状態の名前（PENDING など）は画面の表示名に置き換える
- 例: 「在庫が足りないため注文を確定できません。数量を減らしてやり直してください」
"""

SKILL_EXAMPLES = """# 例

| 悪い | 良い |
| --- | --- |
| 在庫が足りない | 在庫が足りないため注文を確定できません。数量を減らしてやり直してください |
"""

AGENTS_MD = """## ショップのコード

- 注文の振る舞いは SpecLino の仕様 `shop`（`.speclino/specs/shop.md`）が正本
- 図を変えずにコードの呼出しの順序を変えない
- 在庫の確保 → 決済の順で呼ぶ。取消では通知以外の操作を呼ばない
- 例外のメッセージは skill `shop-error-format` に従う
"""


def _policy_package(workspace: str) -> PolicyPackage:
    rule = PolicyRule(
        id=POLICY_RULE_ID,
        version="1",
        level="mandatory",
        title="例外のメッセージは次の行動を示す",
        text=(
            "OrderError のメッセージは、利用者が次に取る行動が分かる文にする"
            "（skill shop-error-format）"
        ),
        applies_to=PolicyApplicability(paths=["examples/shop/app/service.py"]),
        verifier=PolicyVerifierRef(
            id="llm.checklist",
            version="1",
            params={
                "items": [
                    {
                        "id": "next-action",
                        "question": (
                            "OrderError のメッセージに、利用者が次に取る行動が書かれていないか"
                        ),
                    }
                ],
                # 文言を評価するので文字列の単位。送るのは OrderError に渡したリテラルだけ
                # （関数の単位は値を目印に置き換えて送るため、文言の規約には使えない。#114 裁定 37）
                "unit_kind": "string_literal",
                "call_names": ["OrderError"],
            },
        ),
        on_missing_input="unknown",
        tier="llm_assisted",
    )
    return PolicyPackage(
        id="shop-team-core", version="1", status="active", owner=workspace, rules=[rule]
    )


_UNIT_BODY = re.compile(r"```\n(.*)\n```", re.DOTALL)
_ITEM = re.compile(r"^- ([A-Za-z0-9_-]+): ", re.MULTILINE)


@dataclass
class ScriptedChecklistLlm:
    """LLM 支援の規約の評価を、コードの文字列だけで決める偽物（実際の LLM を呼ばない）。

    単位（OrderError に渡したメッセージ）が「。」で終わらなければ該当（引用はそのメッセージ）。
    """

    model: str = SEED_LLM_MODEL
    max_output_tokens: int = 100
    calls: int = 0

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        self.calls += 1
        head, _, code = prompt.partition("評価の単位")
        body = _UNIT_BODY.search(code)
        message = body.group(1) if body else ""
        bare = [message] if message and not message.endswith("。") else []
        items = [
            {"item_id": item, "applies": bool(bare), "quote": bare[0] if bare else ""}
            for item in _ITEM.findall(head.partition("チェック項目")[2])
        ]
        return JsonGeneration(json.dumps({"items": items}, ensure_ascii=False), 0, 0)


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeSeedTarget(RuntimeError):
    """本番か遠隔の DB に seed しようとした（接続する前に止める）。"""


def check_seed_target(settings: Settings, *, allow_remote_db: bool = False) -> None:
    """seed してよい環境か。env=prod は常に拒否。DB のホストがローカル（localhost・ループバック・
    unix socket）でなければ、`allow_remote_db` を明示したときだけ通す。"""
    if settings.env == "prod":
        raise UnsafeSeedTarget("SPECLINO_ENV=prod には seed しない")
    url = make_url(settings.database_url)
    host = url.host or url.query.get("host")
    if isinstance(host, tuple):
        host = host[0] if host else None
    local = host is None or host in _LOCAL_HOSTS or str(host).startswith("/")
    if not local and not allow_remote_db:
        raise UnsafeSeedTarget(
            f"DB のホスト {host} はローカルではない（遠隔の DB に入れるなら --allow-remote-db）"
        )


@dataclass(frozen=True)
class Names:
    """作るものの名前（テストは別の名前で何度でも作る）。"""

    workspace: str = WORKSPACE
    repo: str = REPO
    github_repo_id: int = GITHUB_REPO_ID
    llm_model: str = SEED_LLM_MODEL


@dataclass
class SeedResult:
    workspace: str
    project_id: str
    repo_dir: Path
    bundle_generation: int
    artifacts: dict[str, str]  # 名前 → artifact ID
    runs: dict[str, str] = field(default_factory=dict)  # ブランチ → run ID


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=SpecLino Seed",
         "-c", "user.email=seed@example.invalid", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def init_repo(repo_dir: Path) -> str:
    """main に適合する実装を置いた git リポジトリを作る。main の SHA を返す。"""
    if repo_dir.exists():
        raise ValueError(f"{repo_dir} は既にある（消してから実行する）")
    materialize(repo_dir)
    (repo_dir / "README.md").write_text(
        "# example/shop\n\nSpecLino のショップ題材（examples/shop から生成）。\n"
    )
    _git(repo_dir, "init", "-q", "-b", "main")
    return _commit_all(repo_dir, "feat: ショップの注文サービス")


def add_mutation_branches(repo_dir: Path) -> dict[str, str]:
    """main から `pr/<変異>` を作る。ブランチ → head SHA。"""
    heads: dict[str, str] = {}
    for name in mutation_names():
        branch = f"pr/{name}"
        _git(repo_dir, "checkout", "-q", "-b", branch, "main")
        apply_mutation(repo_dir, name)
        heads[branch] = _commit_all(repo_dir, f"change: {name}")
    _git(repo_dir, "checkout", "-q", "main")
    return heads


async def _draft_and_approve(
    f: async_sessionmaker[AsyncSession],
    n: Names,
    kind: ArtifactKind,
    name: str,
    content: ArtifactContent,
) -> tuple[str, int]:
    async with f() as s, s.begin():
        draft = await kr.create_draft(
            s, workspace=n.workspace, kind=kind, name=name, content=content, author=AUTHOR
        )
    async with f() as s, s.begin():
        bundle = await kr.approve(
            s,
            artifact_id=draft.artifact_id,
            expected_revision=draft.revision,
            expected_hash=draft.content_hash,
            approver=OWNER,
        )
    return draft.artifact_id, bundle.generation


async def seed_registry(
    f: async_sessionmaker[AsyncSession], n: Names
) -> tuple[str, dict[str, str], int]:
    """ワークスペース・プロジェクト・知識・規約を作る。(project ID, 名前 → artifact ID, 世代)。"""
    async with f() as s, s.begin():
        project, _ = await ProjectRepository(s).resolve_for_webhook(
            github_repo_id=n.github_repo_id, repo_full_name=n.repo, installation_id=None
        )
        project_id = str(project.id)
        await wa.create_workspace(s, slug=n.workspace, name="Shop team", owner_uid=OWNER)
        await wa.add_member(s, slug=n.workspace, uid=AUTHOR, role="member")
        await wa.add_project(s, slug=n.workspace, repo_full_name=n.repo)
    scope = ArtifactScope(projects=[project_id])
    contents: list[tuple[ArtifactKind, str, ArtifactContent]] = [
        (
            "spec",
            "shop",
            SpecContent(
                kind="spec", markdown=SPEC.read_text(), domain=load_domain(DOMAIN), scope=scope
            ),
        ),
        (
            "skill",
            "shop-error-format",
            SkillContent(
                kind="skill", skill_md=SKILL_MD, files={"examples.md": SKILL_EXAMPLES}, scope=scope
            ),
        ),
        (
            "instructions",
            "shop-agents",
            InstructionsContent(
                kind="instructions", target="agents_md", body=AGENTS_MD, scope=scope
            ),
        ),
    ]
    ids: dict[str, str] = {}
    generation = 0
    for kind, name, content in contents:
        ids[name], generation = await _draft_and_approve(f, n, kind, name, content)
    async with f() as s, s.begin():
        draft = await pg.create_draft(
            s,
            team=n.workspace,
            package=_policy_package(n.workspace),
            author=AUTHOR,
            note="LLM 支援の規約",
        )
    async with f() as s, s.begin():
        await pg.approve(s, team=n.workspace, revision=draft.revision, approver=OWNER)
    return project_id, ids, generation


async def sync_main(
    f: async_sessionmaker[AsyncSession], n: Names, repo_dir: Path, project_id: str
) -> str:
    """今の bundle を main に書き出し（`speclino sync` と同じ計画と実行スクリプト）、コミット。"""
    async with f() as s, s.begin():
        bundle = await kr.ensure_bundle(s, n.workspace)
        resolved = await kr.resolve_for_project(s, bundle, project_id)
    lock_path = repo_dir / ks.LOCK_PATH
    previous = ks.verified_previous_lock(
        lock_path.read_text() if lock_path.is_file() else None,
        workspace=n.workspace,
        github_repo_id=n.github_repo_id,
    )
    plan = ks.build_plan(
        resolved, github_repo_id=n.github_repo_id, repo_full_name=n.repo, previous=previous
    )
    result = sync_apply.apply_plan(repo_dir, plan.model_dump(mode="json"))
    if not result.ok:
        raise RuntimeError(f"知識を書き出せない: {result.reason}")
    return _commit_all(repo_dir, f"chore: speclino sync（bundle {bundle.generation}）")


async def _active_policy(f: async_sessionmaker[AsyncSession], team: str) -> PolicyRevision | None:
    async with f() as s:
        return await PolicyRevisionRepository(s).active_revision(team)


async def attest_baseline_no_issue(
    f: async_sessionmaker[AsyncSession], n: Names, run_id: Any, project_id: Any
) -> int:
    """main の検査の LLM 支援の「指摘なし」を owner が確定する（チームが main の評価を一度
    確かめた前提）。確定した件数を返す。"""
    async with f() as s:
        rows = await FindingRepository(s).list_for_run(run_id)
    targets = sorted(
        {
            t
            for row in rows
            if (x := Finding.model_validate(row.payload)).assessment_outcome == "no_issue"
            and (t := attestation_target(x)) is not None
        }
    )
    async with f() as s, s.begin():
        for fingerprint, content_hash in targets:
            await attest(
                s,
                fingerprint=fingerprint,
                content_hash=content_hash,
                disposition="attested",
                actor=OWNER,
                workspace=n.workspace,
                project_id=project_id,
                via="seed",
            )
    return len(targets)


async def run_checks(
    f: async_sessionmaker[AsyncSession],
    n: Names,
    *,
    repo_dir: Path,
    project_id: str,
    main_sha: str,
    heads: dict[str, str],
    artifact_dir: Path,
) -> dict[str, str]:
    """main（push）と各 PR の検査を、本番の worker と同じ ReviewRunService で実行する。"""
    import uuid

    host = LocalCodeHost(repos={n.repo: repo_dir}, repo_ids={n.repo: n.github_repo_id})
    holder: dict[str, ReviewRunService] = {}
    service = ReviewRunService(
        session_factory=f,
        code_host=host,
        queue=InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True),
        blobs=FileSystemBlobStore(artifact_dir),
        config=ReviewRunConfig(
            agent_enabled=False,
            resolve_team_policy=lambda team: _active_policy(f, team),
            resolve_knowledge=make_knowledge_resolver(f),
            policy_llm=ScriptedChecklistLlm(model=n.llm_model),
        ),
    )
    holder["svc"] = service
    pid = uuid.UUID(project_id)
    baseline = await service.create_run(
        project_id=pid, head_sha=main_sha, base_sha=None, pr_number=None, trigger="push"
    )
    await attest_baseline_no_issue(f, n, baseline, pid)
    runs = {
        "main": str(
            await service.create_run(
                project_id=pid,
                head_sha=main_sha,
                base_sha=None,
                pr_number=None,
                trigger="attestation",
            )
        )
    }
    for number, (branch, sha) in enumerate(sorted(heads.items()), start=1):
        run_id = await service.create_run(
            project_id=pid,
            head_sha=sha,
            base_sha=main_sha,
            pr_number=number,
            trigger="pull_request.opened",
        )
        runs[branch] = str(run_id)
    return runs


async def seed(
    f: async_sessionmaker[AsyncSession],
    *,
    repo_dir: Path,
    artifact_dir: Path,
    names: Names | None = None,
) -> SeedResult:
    n = names or Names()
    init_repo(repo_dir)
    project_id, ids, _ = await seed_registry(f, n)
    main_sha = await sync_main(f, n, repo_dir, project_id)
    heads = add_mutation_branches(repo_dir)
    runs = await run_checks(
        f,
        n,
        repo_dir=repo_dir,
        project_id=project_id,
        main_sha=main_sha,
        heads=heads,
        artifact_dir=artifact_dir,
    )
    async with f() as s:
        bundle = await kr.current_bundle(s, n.workspace)
    assert bundle is not None
    return SeedResult(n.workspace, project_id, repo_dir, bundle.generation, ids, runs)


__all__ = [
    "GITHUB_REPO_ID",
    "Names",
    "OWNER",
    "REPO",
    "SEED_LLM_MODEL",
    "WORKSPACE",
    "ScriptedChecklistLlm",
    "SeedResult",
    "UnsafeSeedTarget",
    "check_seed_target",
    "seed",
]
