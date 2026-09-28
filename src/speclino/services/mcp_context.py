"""MCP の `context_get` が返す語彙・規約・要求の組み立て（S33 / S44、S54 で worker に移した）。

スナップショットの取得には GitHub App の秘密鍵が要り、秘密鍵は worker だけが持つ
（`api/deps.LazyCodeHost` の docstring）。そのため本番では web が OIDC で worker の
`POST /internal/mcp/context` を呼び、worker がここを実行する
（`adapters/worker/context_client.py`）。
ローカル（role=all）は同じ処理をプロセス内で呼ぶ（`InProcessContextSource`）。
呼び出し側（MCP のツール）は、project がトークンのワークスペースのものであることを先に確かめ、
認可済みの project_id だけを渡す。チーム規約は project の登録先から引く。
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.repositories.projects import ProjectRepository
from speclino.domain.policy import PolicyRevision
from speclino.ports.code_host import CodeHost
from speclino.services.workspaces import repository_mismatch
from speclino.spec import load_spec_ir

TeamPolicyResolver = Callable[[str], Awaitable[PolicyRevision | None]]


class ContextUnavailable(Exception):
    """context を返せない（project が無い、リポジトリ ID が登録時と違う等）。

    理由は利用者に見せてよい。
    """


class InProcessContextSource:
    """同じプロセスで context を組み立てる（ローカル、および worker の内部エンドポイント）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        code_host: CodeHost,
        resolve_team_policy: TeamPolicyResolver | None,
    ) -> None:
        self.session_factory = session_factory
        self.code_host = code_host
        self.resolve_team_policy = resolve_team_policy

    async def get(self, project_id: uuid.UUID, workspace: str, ref: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            project = await ProjectRepository(session).get(project_id)
        # 呼び出し側（web）が確かめたワークスペースとここでも照合する。確認の後に project が別の
        # ワークスペースへ移されても、元のトークンに移し先の規約・仕様を返さない
        if project is None or project.team != workspace:
            raise ContextUnavailable("このワークスペースに登録されていない")
        repo = project.repo_full_name
        # 名前で取得する前に、登録時のリポジトリと同じものかを確かめる（worker と同じ照合）
        mismatch = await repository_mismatch(self.code_host, repo, project.github_repo_id)
        if mismatch is not None:
            raise ContextUnavailable(mismatch)
        # 検証（worker）と同じ解決器・同じチーム（登録先ワークスペース）で読む。ここがずれると、
        # エージェントが読んだ規約とゲートが照らす規約が食い違う。
        team_policy = (
            await self.resolve_team_policy(project.team)
            if self.resolve_team_policy is not None and project.team is not None
            else None
        )
        with tempfile.TemporaryDirectory(prefix="speclino-mcp-") as tmp:
            root = await self.code_host.fetch_snapshot(repo, ref, Path(tmp))
            return read_context(root, team_policy)


def read_context(root: Path, team_policy: PolicyRevision | None = None) -> dict[str, Any]:
    """MCP `context_get` の本体。規約は `check_pipeline._run_policies` と同じ規則で合わせる。

    チーム規約を先に並べ、既に採用した id（チーム規約、または先のファイルのプロジェクト規約）と
    同じ id のプロジェクト規約は無視する（チーム規約をプロジェクト側で無効化できない）。
    無視したものは `ignored_project_policies` に出し、黙って消さない。
    """
    speclino_dir = root / ".speclino"
    out: dict[str, Any] = {
        "domain": None,
        "bindings": None,
        "team_policy": (
            {"team": team_policy.team, "revision": team_policy.revision}
            if team_policy is not None
            else None
        ),
        "policies": [],
        "ignored_project_policies": [],
        "rules": [],
    }
    for name in ("domain", "bindings"):
        path = speclino_dir / f"{name}.yaml"
        if path.is_file():
            out[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    adopted: set[str] = set()
    if team_policy is not None:
        for rule in team_policy.package.rules:
            adopted.add(rule.id)
            out["policies"].append(
                {
                    "id": rule.id,
                    "origin": "team",
                    "level": rule.level,
                    "title": rule.title,
                    "text": rule.text,
                    "verifier": rule.verifier.id if rule.verifier is not None else None,
                    "exceptions_allowed": rule.exceptions_allowed,
                }
            )
    policy_dir = speclino_dir / "policy"
    policy_files = (
        sorted({*policy_dir.glob("*.yaml"), *policy_dir.glob("*.yml")})
        if policy_dir.is_dir()
        else []
    )
    for path in policy_files:
        package = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = package.get("rules", [])
        ignored = [r.get("id") for r in rules if r.get("id") in adopted]
        out["ignored_project_policies"].extend(ignored)
        kept = [r for r in rules if r.get("id") not in ignored]
        adopted.update(r.get("id") for r in kept)
        out["policies"].extend(
            {
                "id": r.get("id"),
                "origin": "project",
                "level": r.get("level"),
                "title": r.get("title"),
                "text": r.get("text"),
                "verifier": (r.get("verifier") or {}).get("id"),
                "exceptions_allowed": r.get("exceptions_allowed", True),
            }
            for r in kept
        )
    ir_dir = root / "specs" / "ir"
    if ir_dir.is_dir():
        for path in sorted(ir_dir.glob("*.spec-ir.json")):
            spec = load_spec_ir(path)
            out["rules"].extend(
                {
                    "id": r.id,
                    "behavior_id": r.behavior_id,
                    "title": r.title,
                    "level": r.level,
                    "modality": r.modality,
                    "approved": r.approval_ref is not None,
                    "spec": next((s.path for s in r.source_refs if s.path), None),
                }
                for r in spec.rules
            )
    return out


__all__ = ["ContextUnavailable", "InProcessContextSource", "TeamPolicyResolver", "read_context"]
