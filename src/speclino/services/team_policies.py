"""チーム規約の画面（S43）の読み出し。書き込みの判断は services/policy_governance.py。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.policy_view import PoliciesView, RuleResult
from speclino.domain.project_overview import PolicyView
from speclino.ports.blob_store import BlobStore
from speclino.services.policy_governance import KNOWN_VERIFIERS, draft_hash
from speclino.services.project_overview import latest_assembled, worst_verdict
from speclino.services.workspaces import require_member


def aggregate_results(per_project: list[list[PolicyView]]) -> dict[str, RuleResult]:
    """各 project の最新の検査の規約の結果を、規約ごとに集計する。

    結果の無い project は数えない。
    """
    grouped: dict[str, list[PolicyView]] = {}
    for views in per_project:
        for v in views:
            # リポジトリ固有の規約に同じ ID があっても、チーム規約の成否には入れない
            if v.origin == "team":
                grouped.setdefault(v.rule_id, []).append(v)
    out: dict[str, RuleResult] = {}
    for rule_id, views in grouped.items():
        decided = [v for v in views if v.status is not None]
        out[rule_id] = RuleResult(
            status=worst_verdict(v.status for v in decided if v.status is not None),
            projects=len(decided),
            failing_projects=sum(1 for v in decided if v.status != "VERIFIED_IN_SCOPE"),
        )
    return out


async def load_policies(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str
) -> PoliciesView:
    await require_member(session, slug, uid)
    revisions = PolicyRevisionRepository(session)
    active = await revisions.active_revision(slug)
    projects = (await session.execute(select(Project).where(Project.team == slug))).scalars()
    per_project: list[list[PolicyView]] = []
    for p in projects:
        _, _, assembled = await latest_assembled(session, blobs, p)
        # 今の有効な改訂で検査した結果だけ（規約を変えた後の古い結果を今の成否として見せない）
        if (
            assembled is not None
            and active is not None
            and assembled.team_name == slug
            and assembled.revision == active.revision
        ):
            per_project.append(assembled.policies)
    draft = await revisions.current_draft(slug)
    return PoliciesView(
        active=active,
        draft=draft,
        draft_hash=draft_hash(draft) if draft is not None else None,
        can_approve=uid in await TeamRepository(session).list_approvers(slug),
        verifiers=dict(KNOWN_VERIFIERS),
        results=aggregate_results(per_project),
    )
