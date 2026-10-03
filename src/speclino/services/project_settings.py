"""project の設定とオンボーディングの段階（S47、design/2026-09-26-team-policies-and-dx.md 4 章）。

- 設定（`projects.settings`）: メンバーなら読める。変更は owner / approver だけ（それ以外は
  `NotPermitted` で拒否し、拒否も監査に残す）。保存の前に `ProjectSettings` で検証する
- ただし `approvers`（PR コメントの approve-spec / answer / attest を受け付ける GitHub ログイン名。
  webhook がこれを信じる）を変えられるのは owner だけ。approver が自分や協力者を足せると、仕様の
  承認や人の証拠を注入できてしまう。approver が承認者を据え置いたまま他の項目を変えるのは通す
- オンボーディング: 「GitHub App を入れる → `.speclino/` を置く → PR を開く」の各段階を、web の
  役割が既に持っているデータ（`projects.installation_id`・run の状態・失敗の理由）だけから導く。
  `.speclino/` の有無を直接確かめるには GitHub App の秘密鍵が要り、鍵は worker だけが持つ
  （design/2026-09-27-workspaces.md 8.2 節）。そこで、完了した検査があれば「ある」、最新の検査が
  `.speclino/` の不足で失敗していれば「無い」、どちらでもなければ「まだ確かめられない」とする
  （推測で埋めない）

非メンバー・別ワークスペースの project は `WorkspaceNotFound`（呼び出し側は 404）。
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project, ReviewRun, RunEvent
from speclino.db.repositories.audit import AuditRepository
from speclino.domain.project_settings import (
    OnboardingStep,
    ProjectOnboarding,
    ProjectSettings,
    ProjectSettingsView,
    StoredProjectSettings,
    WorkspaceOnboarding,
)
from speclino.domain.workspace import APPROVER_ROLES, WorkspaceRole
from speclino.services.workspaces import NotPermitted, WorkspaceNotFound, record_denial, role_of

# 保存値が型に合わないときの値: ゲートは既定のまま、証拠を増やす機能だけを止める（安全側）。
# 承認者も空（誰も approve-spec できない）
LOCKED_DOWN = ProjectSettings(agent_enabled=False, test_run_enabled=False, approvers=[])

# 検査が完了した（判定が出た）状態
_FINISHED_OK = ("completed", "awaiting_answer")

# run の失敗理由（`check_pipeline.run_check` の CheckInputError）→ 画面に出す文言。
# 失敗理由には作業ディレクトリの一時パスが入るので、そのまま出さない
_SETUP_FAILURES = (
    ("domain.yaml が無い", ".speclino/domain.yaml が見つかりません"),
    ("bindings.yaml が無い", ".speclino/bindings.yaml が見つかりません"),
    ("Spec IR が無い", "specs/ir/*.spec-ir.json（承認済みの仕様）が見つかりません"),
)


def parse_stored(raw: dict[str, object]) -> StoredProjectSettings:
    """`projects.settings` の保存値を読む。型に合わなければ `LOCKED_DOWN` と invalid=True。"""
    try:
        return StoredProjectSettings(settings=ProjectSettings.model_validate(raw))
    except ValidationError:
        return StoredProjectSettings(settings=LOCKED_DOWN, invalid=True)


def derive_onboarding(
    *,
    installation_id: int | None,
    has_completed_run: bool,
    has_any_run: bool,
    latest_failure: str | None,
) -> list[OnboardingStep]:
    """オンボーディングの 3 段階（モジュールの docstring の規則）。"""
    if installation_id is not None:
        app = OnboardingStep(
            id="app", state="done", detail="GitHub App がインストールされ、リポジトリが登録済み"
        )
    else:
        app = OnboardingStep(
            id="app",
            state="unknown",
            detail="GitHub を経由せずに登録されたため、インストール状態を確かめられません",
        )

    setup_problem = next(
        (msg for marker, msg in _SETUP_FAILURES if latest_failure and marker in latest_failure),
        None,
    )
    if has_completed_run:
        speclino = OnboardingStep(
            id="speclino", state="done", detail="検査が完了し、設定ファイルを読めました"
        )
    elif setup_problem is not None:
        speclino = OnboardingStep(
            id="speclino", state="todo", detail=f"最新の検査で {setup_problem}"
        )
    else:
        speclino = OnboardingStep(
            id="speclino",
            state="unknown",
            detail="最初の検査が終わると確かめられます",
        )

    if has_completed_run:
        first_run = OnboardingStep(id="first_run", state="done", detail="検査が完了しています")
    elif has_any_run:
        first_run = OnboardingStep(
            id="first_run", state="todo", detail="検査は始まっていますが、まだ完了していません"
        )
    else:
        first_run = OnboardingStep(
            id="first_run", state="todo", detail="PR を開くと検査が始まります"
        )
    return [app, speclino, first_run]


async def _member_role(session: AsyncSession, slug: str, uid: str) -> WorkspaceRole:
    role = await role_of(session, slug, uid)
    if role is None:
        raise WorkspaceNotFound(slug)
    return role


async def _project_in(session: AsyncSession, slug: str, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.team != slug:
        raise WorkspaceNotFound(slug)
    return project


class ApproversOwnerOnly(NotPermitted):
    """owner でない人が PR の承認者（`approvers`）を変えようとした。"""


def _view(project: Project, role: WorkspaceRole) -> ProjectSettingsView:
    stored = parse_stored(project.settings)
    return ProjectSettingsView(
        project_id=project.id,
        repo_full_name=project.repo_full_name,
        settings=stored.settings,
        stored_invalid=stored.invalid,
        can_edit=role in APPROVER_ROLES,
        can_edit_approvers=role == "owner",
    )


async def get_settings(
    session: AsyncSession, *, slug: str, uid: str, project_id: uuid.UUID
) -> ProjectSettingsView:
    role = await _member_role(session, slug, uid)
    return _view(await _project_in(session, slug, project_id), role)


async def update_settings(
    session: AsyncSession,
    *,
    slug: str,
    uid: str,
    project_id: uuid.UUID,
    settings: ProjectSettings,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ProjectSettingsView:
    """設定を置き換える。owner / approver 以外は `NotPermitted`、owner 以外が `approvers` を
    変えようとしたら `ApproversOwnerOnly`（どちらも拒否を監査に残し、何も書かない）。"""
    role = await _member_role(session, slug, uid)
    project = await _project_in(session, slug, project_id)
    if role not in APPROVER_ROLES:
        await record_denial(
            session,
            audit_factory,
            actor=uid,
            action="project.settings",
            target=project.repo_full_name,
            detail={"reason": "not_an_approver", "role": role, "workspace": slug},
        )
        raise NotPermitted(f"{uid} は {slug} の設定を変更できない")
    # 比べる相手は保存値そのもの（型に合わない保存値を LOCKED_DOWN の空リストと見なすと、
    # 空で送れば「据え置き」に見えてしまう）。違えば何も書かずに拒否する
    if role != "owner" and settings.approvers != project.settings.get("approvers", []):
        await record_denial(
            session,
            audit_factory,
            actor=uid,
            action="project.settings",
            target=project.repo_full_name,
            detail={
                "reason": "approvers_owner_only",
                "role": role,
                "workspace": slug,
                "requested_approvers": settings.approvers,
            },
        )
        raise ApproversOwnerOnly(f"{uid} は {slug} の PR の承認者を変更できない")
    before = parse_stored(project.settings)
    after = settings.model_dump(mode="json")
    project.settings = after
    await session.flush()
    await AuditRepository(session).record(
        uid,
        "project.settings",
        project.repo_full_name,
        "updated",
        {
            "workspace": slug,
            "before": before.settings.model_dump(mode="json"),
            "before_invalid": before.invalid,
            "after": after,
        },
    )
    return _view(project, role)


async def _latest_failure(session: AsyncSession, project_id: uuid.UUID) -> str | None:
    """project の最新の run が失敗していれば、その理由（最後の state イベントの error）。"""
    latest = (
        await session.execute(
            select(ReviewRun.id, ReviewRun.exec_state)
            .where(ReviewRun.project_id == project_id)
            .order_by(ReviewRun.created_at.desc())
            .limit(1)
        )
    ).first()
    if latest is None or latest.exec_state != "failed":
        return None
    payload = (
        await session.execute(
            select(RunEvent.payload)
            .where(RunEvent.run_id == latest.id, RunEvent.type == "state")
            .order_by(RunEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    error = payload.get("error") if payload else None
    return error if isinstance(error, str) else None


async def workspace_onboarding(
    session: AsyncSession, *, slug: str, uid: str
) -> WorkspaceOnboarding:
    role = await _member_role(session, slug, uid)
    projects = (
        await session.execute(
            select(Project).where(Project.team == slug).order_by(Project.repo_full_name)
        )
    ).scalars()
    out: list[ProjectOnboarding] = []
    for project in projects:
        states = set(
            (
                await session.execute(
                    select(ReviewRun.exec_state)
                    .where(ReviewRun.project_id == project.id)
                    .distinct()
                )
            ).scalars()
        )
        steps = derive_onboarding(
            installation_id=project.installation_id,
            has_completed_run=any(s in _FINISHED_OK for s in states),
            has_any_run=bool(states),
            latest_failure=await _latest_failure(session, project.id),
        )
        out.append(
            ProjectOnboarding(
                project_id=project.id, repo_full_name=project.repo_full_name, steps=steps
            )
        )
    return WorkspaceOnboarding(can_edit_settings=role in APPROVER_ROLES, projects=out)


__all__ = [
    "LOCKED_DOWN",
    "ApproversOwnerOnly",
    "NotPermitted",
    "WorkspaceNotFound",
    "derive_onboarding",
    "get_settings",
    "parse_stored",
    "update_settings",
    "workspace_onboarding",
]
