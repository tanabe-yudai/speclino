"""リポジトリの登録（S55、design/2026-09-27-workspaces.md 6・8.3 節）。

ワークスペースの owner が、GitHub のユーザー認可を経て、自分が admin で SpecLino の GitHub App が
インストールされているリポジトリをワークスペースに登録する。
- 試行（state・PKCE の verifier）はサーバーに保存し、コールバックで 1 回だけ消費する。state は
  SHA-256 だけ保存する
- 本人の admin とインストールは、ユーザートークンで `/user/installations` 配下に列挙されることで
  確かめる（`GET /repos` の 200 は公開リポジトリなら誰でも得られるので使わない）
- 登録（project の解決・名前の退避・条件付き更新・監査）は 1 トランザクション。拒否なら全体を戻す
- ユーザートークンは保存しない。確認が済んだら認可ごと取り消し（access / refresh token が失効）、
  **取り消せたときだけ登録する**。拒否・例外の経路でも取り消しを試みる
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.adapters.github.user_oauth import GitHubOAuthError, GitHubUserOAuth, authorize_url
from speclino.db.models import Project, RepoLinkAttempt
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.workspace import (
    AccessibleRepository,
    RegisteredRepository,
    RepositoryRegistration,
)
from speclino.services.workspaces import require_member, require_owner, resolve_github_project

logger = logging.getLogger(__name__)

ATTEMPT_TTL = timedelta(minutes=10)
_REPO_FULL_NAME = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class RegistrationError(Exception):
    """登録できない。code は API のエラーコード、文面は利用者に見せてよい。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


async def start(
    session: AsyncSession,
    *,
    slug: str,
    uid: str,
    repo_full_name: str,
    client_id: str,
    redirect_uri: str,
) -> str:
    """試行を保存し、GitHub の認可 URL を返す。owner 以外は `WorkspaceNotFound`。"""
    await require_owner(session, slug, uid)
    if not _REPO_FULL_NAME.fullmatch(repo_full_name):
        raise RegistrationError("invalid_repository", "リポジトリは owner/name の形で指定する")
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    session.add(
        RepoLinkAttempt(
            state_hash=_hash(state),
            uid=uid,
            team=slug,
            repo_full_name=repo_full_name,
            code_verifier=code_verifier,
            expires_at=datetime.now(UTC) + ATTEMPT_TTL,
        )
    )
    await session.flush()
    return authorize_url(
        client_id=client_id, redirect_uri=redirect_uri, state=state, code_verifier=code_verifier
    )


async def complete(
    session_factory: async_sessionmaker[AsyncSession],
    oauth: GitHubUserOAuth,
    *,
    uid: str,
    code: str,
    state: str,
    redirect_uri: str,
) -> RepositoryRegistration:
    """コールバック: 試行を消費し、GitHub で確かめて登録する。"""
    # 試行の消費は先に確定させる（以後の段階で失敗しても、同じ state は二度と使えない）
    async with session_factory() as session, session.begin():
        attempt = await _consume(session, state=state, uid=uid)
    slug, repo_full_name, code_verifier = attempt

    token: str | None = None
    revoked = False
    try:
        try:
            token = await oauth.exchange(
                code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
            )
            repo = await oauth.find_repository(token, repo_full_name)
        except GitHubOAuthError as e:
            raise RegistrationError("github_denied", str(e)) from e
        if repo is None:
            raise RegistrationError(
                "not_installed",
                f"{repo_full_name} に SpecLino の GitHub App が入っていないか、アクセスできない",
            )
        if not repo.admin:
            raise RegistrationError("not_admin", f"{repo_full_name} の admin ではない")
        # トークンはもう要らない。取り消せなかったら登録しない
        # （有効なトークンを残したまま成功にしない）
        try:
            await oauth.revoke(token)
        except GitHubOAuthError as e:
            raise RegistrationError(
                "github_revoke_failed",
                "GitHub の認可を取り消せなかったので登録しなかった。時間をおいてやり直してください",
            ) from e
        revoked = True
        async with session_factory() as session, session.begin():
            status = await _register(session, slug=slug, uid=uid, repo=repo)
        return RepositoryRegistration(slug=slug, repo_full_name=repo.full_name, status=status)
    finally:
        if token is not None and not revoked:
            await _revoke_quietly(oauth, token)


async def list_repositories(
    session: AsyncSession, *, slug: str, uid: str
) -> list[RegisteredRepository]:
    await require_member(session, slug, uid)
    stmt = select(Project).where(Project.team == slug).order_by(Project.repo_full_name)
    return [
        RegisteredRepository(
            repo_full_name=p.repo_full_name,
            github_repo_id=p.github_repo_id,
            default_branch=p.default_branch,
        )
        for p in (await session.execute(stmt)).scalars()
    ]


async def _consume(session: AsyncSession, *, state: str, uid: str) -> tuple[str, str, str]:
    stmt = (
        update(RepoLinkAttempt)
        .where(
            RepoLinkAttempt.state_hash == _hash(state),
            RepoLinkAttempt.uid == uid,
            RepoLinkAttempt.consumed_at.is_(None),
            RepoLinkAttempt.expires_at > datetime.now(UTC),
        )
        .values(consumed_at=datetime.now(UTC))
        .returning(
            RepoLinkAttempt.team, RepoLinkAttempt.repo_full_name, RepoLinkAttempt.code_verifier
        )
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise RegistrationError(
            "invalid_state",
            "登録の手続きが無効（期限切れ・使用済み・別のアカウント）。やり直してください",
        )
    return str(row[0]), str(row[1]), str(row[2])


async def _register(
    session: AsyncSession, *, slug: str, uid: str, repo: AccessibleRepository
) -> Literal["registered", "already_registered"]:
    # 認可の間に owner でなくなっていないかを確かめ直す
    await require_owner(session, slug, uid)
    project = await resolve_github_project(
        session,
        github_repo_id=repo.github_repo_id,
        repo_full_name=repo.full_name,
        installation_id=repo.installation_id,
        default_branch=repo.default_branch,
        actor=uid,
    )
    result = await session.execute(
        update(Project).where(Project.id == project.id, Project.team.is_(None)).values(team=slug)
    )
    if cast(CursorResult[Any], result).rowcount == 0:
        current = (
            await session.execute(select(Project.team).where(Project.id == project.id))
        ).scalar_one()
        if current != slug:
            # 例外で抜けて、退避などを含めてこのトランザクションを全部戻す
            raise RegistrationError(
                "registered_elsewhere", f"{repo.full_name} は別のワークスペースに登録済み"
            )
        return "already_registered"
    await AuditRepository(session).record(
        uid,
        "project.register",
        slug,
        "registered",
        {"repo": repo.full_name, "github_repo_id": repo.github_repo_id},
    )
    return "registered"


async def _revoke_quietly(oauth: GitHubUserOAuth, token: str) -> None:
    try:
        await oauth.revoke(token)
    except Exception as e:  # noqa: BLE001 - 失効の失敗で登録結果を変えない。値は出さない
        logger.warning("GitHub のユーザートークンを失効できなかった: %s", type(e).__name__)


__all__ = ["ATTEMPT_TTL", "RegistrationError", "complete", "list_repositories", "start"]
