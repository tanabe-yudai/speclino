"""ワークスペースの管理操作（S53、design/2026-09-27-workspaces.md）。

管理 CLI（`speclino workspace ...`）と本番の Cloud Run Job（`speclino-admin`）から呼ばれる。
`services/policy_governance.py` と同じ流儀で、DB 操作は repositories に委ね、ここでは
「何を確認し、何を監査に残すか」というビジネスロジックだけを持つ。呼び出し側は
`async with session_factory() as session, session.begin(): ...` の1トランザクションで包んで
呼ぶ。操作が拒否される（`ValueError` を投げる）場合、そのトランザクションは呼び出し側の
rollback で消えるので、監査にも残らない（拒否そのものを追跡したい場合は呼び出し側で別途
記録する。CLI は失敗を終了コードとメッセージで返すだけで十分なため、ここでは分けない）。

登録（`add_project`）は `UPDATE ... WHERE team IS NULL` の条件付き更新で原子的に取る
（1 リポジトリは高々 1 ワークスペース）。
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, TeamRecord, WorkspaceMember
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.workspace import (
    BackfillIsolationReason,
    BackfillResult,
    WorkspaceMemberInfo,
    WorkspaceRole,
    WorkspaceSummary,
)
from speclino.ports.code_host import CodeHost
from speclino.ports.github_users import GitHubUserLookup
from speclino.services.project_settings import resolve_approver_ids

# 管理 CLI / Cloud Run Job からの操作の actor（人手の承認ではなく管理者の操作のため固定値）。
ADMIN_ACTOR = "admin-cli"

# 改名・削除後に同名で作られた別リポジトリとの衝突を退避した行の目印
# （webhook 側の同一性解決と同じ命名規則。design/2026-09-27-workspaces.md 5章）。
_STALE_MARK = "#stale-"


async def create_workspace(
    session: AsyncSession, *, slug: str, name: str | None, owner_uid: str
) -> None:
    """ワークスペース（`teams` 行）を作り、`owner_uid` を owner として登録する。

    S42 の規約操作で作られた、メンバーのいない `teams` 行はそのままワークスペースとして引き取る
    （名前と owner を設定する。その行の規約改訂は引き継ぐ）。メンバーがいる＝既にワークスペース
    として使われている slug なら `ValueError`（メンバーの変更は `add_member` で行う）。
    """
    teams = TeamRepository(session)
    await teams.ensure_and_lock(slug)
    if await teams.has_members(slug):
        raise ValueError(f"ワークスペース {slug} は既に存在する")
    await teams.set_name(slug, name)
    await teams.set_member(slug, owner_uid, "owner")
    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="workspace.create",
        target=slug,
        decision="created",
        detail={"name": name, "owner_uid": owner_uid},
    )


async def add_member(session: AsyncSession, *, slug: str, uid: str, role: WorkspaceRole) -> None:
    """`uid` を `slug` のメンバーにする（既にいれば role を置き換える）。

    ワークスペースが無ければ `ValueError`（存在しないワークスペースへの追加を CLI の
    typo で黙って新規作成してしまわないようにする）。
    """
    if await TeamRepository(session).get(slug) is None:
        raise ValueError(f"ワークスペース {slug} が存在しない")
    await TeamRepository(session).set_member(slug, uid, role)
    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="workspace.add_member",
        target=slug,
        decision="set",
        detail={"uid": uid, "role": role},
    )


async def add_project(session: AsyncSession, *, slug: str, repo_full_name: str) -> None:
    """`repo_full_name` の project を `slug` のワークスペースに登録する。

    条件:
      - project が存在すること（無ければ `ValueError`。API/webhook から新規作成しない
        方針と同じく、ここでも project は作らない）
      - `github_repo_id` が埋まっていること（NULL なら `backfill-repo-ids` を先に、と
        案内して `ValueError`）
      - まだどのワークスペースにも登録されていないこと。`UPDATE ... WHERE team IS NULL`
        の条件付き更新で原子的に取る。既に別のワークスペースに登録済みなら `ValueError`。
        既に同じワークスペースに登録済みなら冪等に成功扱いにする（再実行しても壊れない）
    """
    if await TeamRepository(session).get(slug) is None:
        raise ValueError(f"ワークスペース {slug} が存在しない")
    project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if project is None:
        raise ValueError(f"project が存在しない: {repo_full_name}")
    if project.github_repo_id is None:
        raise ValueError(
            f"{repo_full_name} は github_repo_id が未確定。先に backfill-repo-ids を実行する"
        )

    result = await session.execute(
        update(Project).where(Project.id == project.id, Project.team.is_(None)).values(team=slug)
    )
    if cast(CursorResult[Any], result).rowcount == 0:
        # 条件（team IS NULL）に一致しなかった。既に登録済みのはずなので現在値を見る。
        # `project` は ID マップに乗っているため `session.get` は DB へ再照会せず読み込み時の
        # 値（NULL）を返してしまう。列だけを select して確実に現在値を読む。
        current_team = (
            await session.execute(select(Project.team).where(Project.id == project.id))
        ).scalar_one()
        if current_team == slug:
            # 同じワークスペースへの再登録は冪等に成功扱い（design のとおり安全側）。
            return
        raise ValueError(f"{repo_full_name} は既に別のワークスペース {current_team} に登録済み")

    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="workspace.add_project",
        target=slug,
        decision="added",
        detail={"repo": repo_full_name, "github_repo_id": project.github_repo_id},
    )


async def set_pr_approvers(
    session: AsyncSession, *, repo_full_name: str, logins: list[str], users: GitHubUserLookup
) -> None:
    """PR コメントの `/speclino approve-spec` を受け付ける GitHub ログイン名を置き換える。

    ワークスペースの approver ロール（チーム規約の承認、uid）とは別物で、project ごとの
    `settings.approvers` に入る（S52 で全体共通の承認者を廃止した）。S47 からは設定画面の API
    （`services/project_settings.update_settings`、owner / approver だけ）からも変えられる。
    空リストなら誰も承認できない。project が無ければ `ValueError`。

    照合は GitHub のユーザー ID だけで行う（S61）ので、ログイン名から ID を引き直して一緒に
    保存する（古い承認者の ID は使わない）。引けないログイン名があれば `UnknownGitHubLogin`
    （`ValueError`）、問い合わせに失敗したら `GitHubLookupFailed`（どちらも何も書かない）。
    """
    project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if project is None:
        raise ValueError(f"project が存在しない: {repo_full_name}")
    approver_ids = await resolve_approver_ids(users, logins, {})
    before = list(project.settings.get("approvers", []))
    project.settings = {**project.settings, "approvers": logins, "approver_ids": approver_ids}
    await session.flush()
    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="project.set_pr_approvers",
        target=repo_full_name,
        decision="set",
        detail={"before": before, "after": logins, "approver_ids": approver_ids},
    )


async def backfill_approver_ids(
    session: AsyncSession, *, repo_full_name: str, users: GitHubUserLookup, apply: bool = False
) -> list[tuple[str, int]]:
    """保存済みの承認者（ログイン名）に GitHub のユーザー ID を補う（S61）。

    ログイン名と ID の組を返す。`apply` が False なら引くだけで書かない（確認用）。ID は今その
    ログイン名を持つ人のものなので、改名で手放されたログイン名を他人が取っていれば他人の ID になる。
    組を人が確かめてから `apply=True` で保存する。

    ID の無い設定では誰も承認者にならないので、既存の project の移行に使う。既に揃っている
    ログイン名と ID の組はそのまま使う（引き直すと、改名後に同じログイン名を取った別人の ID に
    すり替わりうる）。引けないログイン名があれば `UnknownGitHubLogin`（`ValueError`）。
    保存値が型に合わなければ `ValueError`（設定画面で保存し直す）。
    """
    project = await ProjectRepository(session).get_by_repo(repo_full_name)
    if project is None:
        raise ValueError(f"project が存在しない: {repo_full_name}")
    approvers = project.settings.get("approvers", [])
    if not isinstance(approvers, list) or not all(isinstance(a, str) for a in approvers):
        raise ValueError(f"{repo_full_name} の承認者の保存値が読めない。設定画面で保存し直す")
    approver_ids = await resolve_approver_ids(users, approvers, project.settings)
    pairs = list(zip(approvers, approver_ids, strict=True))
    if not apply:
        return pairs
    project.settings = {**project.settings, "approver_ids": approver_ids}
    await session.flush()
    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="project.backfill_approver_ids",
        target=repo_full_name,
        decision="filled",
        detail={"approvers": approvers, "approver_ids": approver_ids},
    )
    return pairs


async def backfill_repo_ids(session: AsyncSession, code_host: CodeHost) -> list[BackfillResult]:
    """`github_repo_id` が NULL の project 全件に、コードホストから引いた ID を埋める。

    埋められない（コードホストが解決できない、または同じ ID が既に他の行に使われている）
    行は、埋めずに `team` を NULL にして隔離する（design/2026-09-27-workspaces.md の
    codex レビュー #3: 名前一致で ID を自動で埋めると、改名・削除後に同名で作られた別
    リポジトリが旧 project の team・承認者・過去 run を引き継いでしまう）。

    `#stale-` を名前に含む行（webhook の同一性解決が退避した旧行）は対象外。
    """
    stale_pattern = f"%{_STALE_MARK}%"
    stmt = select(Project).where(
        Project.github_repo_id.is_(None),
        Project.repo_full_name.notlike(stale_pattern),
    )
    projects = list((await session.execute(stmt)).scalars())

    results: list[BackfillResult] = []
    for project in projects:
        repo_id = await code_host.repository_id(project.repo_full_name)
        if repo_id is None:
            results.append(await _isolate(session, project, reason="not_found"))
            continue

        conflict_stmt = select(Project.id).where(
            Project.github_repo_id == repo_id, Project.id != project.id
        )
        if (await session.execute(conflict_stmt)).scalar_one_or_none() is not None:
            results.append(await _isolate(session, project, reason="id_conflict"))
            continue

        project.github_repo_id = repo_id
        await session.flush()
        await AuditRepository(session).record(
            actor=ADMIN_ACTOR,
            action="workspace.backfill_repo_ids",
            target=project.repo_full_name,
            decision="filled",
            detail={"github_repo_id": repo_id},
        )
        results.append(BackfillResult(repo=project.repo_full_name, action="filled"))
    return results


async def _isolate(
    session: AsyncSession, project: Project, *, reason: BackfillIsolationReason
) -> BackfillResult:
    """project を隔離する（team を NULL にし、監査に理由を残す）。"""
    project.team = None
    await session.flush()
    await AuditRepository(session).record(
        actor=ADMIN_ACTOR,
        action="workspace.backfill_repo_ids",
        target=project.repo_full_name,
        decision="isolated",
        detail={"reason": reason},
    )
    return BackfillResult(repo=project.repo_full_name, action="isolated", reason=reason)


async def list_workspaces(session: AsyncSession) -> list[WorkspaceSummary]:
    """全ワークスペースの slug・表示名・メンバー・登録 project 名（slug 順）。"""
    teams = (
        await session.execute(select(TeamRecord.team, TeamRecord.name).order_by(TeamRecord.team))
    ).all()

    members_by_team: dict[str, list[WorkspaceMemberInfo]] = {}
    member_rows = (
        await session.execute(
            select(WorkspaceMember.team, WorkspaceMember.uid, WorkspaceMember.role).order_by(
                WorkspaceMember.team, WorkspaceMember.uid
            )
        )
    ).all()
    for team, uid, role in member_rows:
        members_by_team.setdefault(team, []).append(WorkspaceMemberInfo(uid=uid, role=role))

    projects_by_team: dict[str, list[str]] = {}
    project_rows = (
        await session.execute(
            select(Project.team, Project.repo_full_name)
            .where(Project.team.isnot(None))
            .order_by(Project.team, Project.repo_full_name)
        )
    ).all()
    for team, repo in project_rows:
        projects_by_team.setdefault(team, []).append(repo)

    return [
        WorkspaceSummary(
            slug=slug,
            name=name,
            members=tuple(members_by_team.get(slug, [])),
            projects=tuple(projects_by_team.get(slug, [])),
        )
        for slug, name in teams
    ]


__all__ = [
    "ADMIN_ACTOR",
    "add_member",
    "add_project",
    "backfill_approver_ids",
    "backfill_repo_ids",
    "create_workspace",
    "list_workspaces",
    "set_pr_approvers",
]
