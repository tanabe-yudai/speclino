"""知識の配信（S68、design/2026-10-05-team-knowledge-registry.md 7 章）の型。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.knowledge import KnowledgeLock


@dataclass(frozen=True)
class SyncTokenInfo:
    """sync トークンの一覧・発行の応答に出す情報。平文もハッシュも含めない。"""

    id: uuid.UUID
    project_id: uuid.UUID
    repo_full_name: str
    name: str
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedSyncToken:
    """発行直後だけ手に入る平文のトークン（保存しない）。"""

    info: SyncTokenInfo
    token: str


@dataclass(frozen=True)
class SyncIdentity:
    """有効な sync トークンの身元。触れるのはこのワークスペースのこのプロジェクトだけ。"""

    token_id: uuid.UUID
    workspace: str
    project_id: uuid.UUID


class WriteOp(IRModel):
    """ファイル全体を書く。既存の中身が違えば `expected_sha256`（前回の lock の記録）と一致するとき
    だけ上書きする。"""

    op: Literal["write"] = "write"
    path: str
    content: str
    sha256: str
    expected_sha256: str | None = None


class SetManagedBlockOp(IRModel):
    """AGENTS.md / CLAUDE.md の管理区画の中だけを置き換える（無ければ末尾に足す）。"""

    op: Literal["set_managed_block"] = "set_managed_block"
    path: str
    inner: str
    generation: int = Field(ge=1)
    expected_inner_sha256: str | None = None


class RemoveManagedBlockOp(IRModel):
    """管理区画だけを取り除く（中身が前回の lock の記録どおりのときだけ）。

    `expected_inner_sha256` が None のものは配置の片付け（S76）。印が本物の 1 組だけなら
    SpecLino のものとして、中身を問わず取り除く。lock の `absent` には載せない。
    """

    op: Literal["remove_managed_block"] = "remove_managed_block"
    path: str
    expected_inner_sha256: str | None = None


class DeleteOp(IRModel):
    """ファイルを消す（中身が前回の lock の記録どおりのときだけ）。"""

    op: Literal["delete"] = "delete"
    path: str
    expected_sha256: str


SyncOp = Annotated[
    WriteOp | SetManagedBlockOp | RemoveManagedBlockOp | DeleteOp, Field(discriminator="op")
]


InstructionsFile = Literal["AGENTS.md", "CLAUDE.md"]
SkillsRoot = Literal[".agents/skills", ".claude/skills"]
LayoutKind = Literal["agents", "claude", "legacy_dual", "mixed", "unknown"]


@dataclass(frozen=True)
class SyncLayout:
    """書き出し先の配置（S76）。指示書をどのファイルに、スキルをどのディレクトリに書くか。"""

    instructions_file: InstructionsFile = "AGENTS.md"
    skills_root: SkillsRoot = ".agents/skills"


class SyncPlan(IRModel):
    """書き出しの計画（S75、design/2026-10-07-agent-sync.md A3）。サーバが作って署名する。

    実行スクリプト（`speclino_apply.py`）が checkout に当てる。lock は最後に書く。
    削除（`delete` / `remove_managed_block`）は、サーバが署名・ワークスペース・リポジトリ ID を
    確かめた前回の lock からだけ作る。
    """

    schema_version: Literal["0.1"] = "0.1"
    workspace: str
    repo_full_name: str
    github_repo_id: int
    bundle_generation: int = Field(ge=1)
    ops: list[SyncOp] = Field(default_factory=list)
    lock: KnowledgeLock
    key_id: str | None = None
    signature: str = Field(min_length=1)


class SyncBundleRequest(IRModel):
    """`POST /api/sync/bundle` の要求。`lock_json` は手元の `.speclino/lock.json`（削除の根拠）。"""

    repo_id: int
    lock_json: str | None = Field(default=None, max_length=1024 * 1024)
    # 省略すると今の配置を保つ（初回は AGENTS.md と .agents/skills）
    instructions_file: InstructionsFile | None = None
    skills_dir: SkillsRoot | None = None


__all__ = [
    "DeleteOp",
    "InstructionsFile",
    "IssuedSyncToken",
    "LayoutKind",
    "RemoveManagedBlockOp",
    "SetManagedBlockOp",
    "SkillsRoot",
    "SyncBundleRequest",
    "SyncIdentity",
    "SyncLayout",
    "SyncOp",
    "SyncPlan",
    "SyncTokenInfo",
    "WriteOp",
]
