"""ワークスペース（テナント境界）と利用者の型（S53、design/2026-09-27-workspaces.md）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WorkspaceRole = Literal["owner", "approver", "member"]

# チーム規約を承認できる role。owner は approver の権限を含む。
APPROVER_ROLES: tuple[WorkspaceRole, ...] = ("owner", "approver")

# `backfill_repo_ids` が github_repo_id を埋められなかったときの理由。
BackfillIsolationReason = Literal["not_found", "id_conflict"]


@dataclass(frozen=True)
class AuthUser:
    """API の認証済み利用者。

    認可・メンバー・監査の識別子は `uid`（Identity Platform の `sub`）だけを使う。メールは
    表示用。メールをキーにすると、確認していない第三者がそのメールで先にサインアップして
    権限を得られる。
    """

    uid: str
    email: str | None = None
    email_verified: bool = False


@dataclass(frozen=True)
class BackfillResult:
    """`services.workspace_admin.backfill_repo_ids` が1リポジトリ行に対して行った結果。

    action="filled" なら github_repo_id を埋めた。action="isolated" なら埋めずに
    team を NULL にして隔離した（reason に理由。埋めた場合は None）。
    """

    repo: str
    action: Literal["filled", "isolated"]
    reason: BackfillIsolationReason | None = None


@dataclass(frozen=True)
class WorkspaceMemberInfo:
    """ワークスペースのメンバー1人分（`list_workspaces` の表示用）。"""

    uid: str
    role: WorkspaceRole


@dataclass(frozen=True)
class WorkspaceSummary:
    """`services.workspace_admin.list_workspaces` が返す1ワークスペース分の概要。"""

    slug: str
    name: str | None
    members: tuple[WorkspaceMemberInfo, ...]
    projects: tuple[str, ...]


__all__ = [
    "APPROVER_ROLES",
    "AuthUser",
    "BackfillIsolationReason",
    "BackfillResult",
    "WorkspaceMemberInfo",
    "WorkspaceRole",
    "WorkspaceSummary",
]
