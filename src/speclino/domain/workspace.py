"""ワークスペース（テナント境界）と利用者の型（S53、design/2026-09-27-workspaces.md）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
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
    # ID トークンの `name`（Google などの表示名）。プロフィールの初期値にだけ使う
    name: str | None = None


@dataclass(frozen=True)
class UserProfile:
    """利用者のプロフィール（表示用）。認可には使わない。

    display_name は本人が設定した表示名（初回だけ ID トークンの name を入れる）。email と
    email_verified は最後にサインインしたときの ID トークンの値。
    """

    uid: str
    display_name: str | None
    email: str | None
    email_verified: bool


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


@dataclass(frozen=True)
class McpTokenInfo:
    """MCP トークンの一覧・発行の応答に出す情報（S54）。平文もハッシュも含めない。"""

    id: uuid.UUID
    name: str
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedMcpToken:
    """発行直後だけ手に入る平文のトークン（保存しない）。"""

    info: McpTokenInfo
    token: str


@dataclass(frozen=True)
class WorkspaceMemberView:
    """メンバー一覧の 1 人分。

    email はプロフィール（最後のサインインの ID トークン）のメールを優先し、無ければ招待の受諾で
    入った確認済みのメール。email_verified はそのメールが確認済みか。label は画面に出す名前
    （表示名 → メール → uid の短縮）。
    """

    uid: str
    display_name: str | None
    email: str | None
    email_verified: bool
    label: str
    role: WorkspaceRole
    joined_at: datetime


@dataclass(frozen=True)
class InvitationInfo:
    """招待の一覧・発行の応答に出す情報（S56）。平文のトークンもハッシュも含めない。"""

    id: uuid.UUID
    email: str
    role: WorkspaceRole
    created_by: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedInvitation:
    """発行直後だけ手に入る平文のトークン（保存しない）。招待リンクはこれから作る。"""

    info: InvitationInfo
    token: str


@dataclass(frozen=True)
class InvitationPreview:
    """招待リンクを開いた人に見せる内容（受諾の前の確認）。"""

    slug: str
    name: str | None
    email: str
    role: WorkspaceRole
    expires_at: datetime


InvitationAcceptStatus = Literal["joined", "already_member"]


@dataclass(frozen=True)
class AcceptedInvitation:
    """受諾の結果。already_member なら既存の role のまま（招待で上書きしない）。"""

    slug: str
    role: WorkspaceRole
    status: InvitationAcceptStatus


@dataclass(frozen=True)
class AccessibleRepository:
    """GitHub App のユーザートークンで見えた、App がインストール済みのリポジトリ（S55）。

    `admin` はそのユーザーのリポジトリでの権限（`GET /user/installations/{id}/repositories` の
    `permissions.admin`）。
    """

    github_repo_id: int
    full_name: str
    default_branch: str
    installation_id: int
    admin: bool


@dataclass(frozen=True)
class RepositoryRegistration:
    """リポジトリ登録の結果（S55）。status は registered か already_registered。"""

    slug: str
    repo_full_name: str
    status: Literal["registered", "already_registered"]


@dataclass(frozen=True)
class RegisteredRepository:
    repo_full_name: str
    github_repo_id: int | None
    default_branch: str


__all__ = [
    "APPROVER_ROLES",
    "AcceptedInvitation",
    "InvitationAcceptStatus",
    "InvitationInfo",
    "InvitationPreview",
    "IssuedInvitation",
    "WorkspaceMemberView",
    "AccessibleRepository",
    "RegisteredRepository",
    "RepositoryRegistration",
    "AuthUser",
    "UserProfile",
    "IssuedMcpToken",
    "McpTokenInfo",
    "BackfillIsolationReason",
    "BackfillResult",
    "WorkspaceMemberInfo",
    "WorkspaceRole",
    "WorkspaceSummary",
]
