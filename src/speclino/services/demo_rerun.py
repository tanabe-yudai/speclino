"""デモ再実行（S39 / ハッカソン計画「審査員体験」）。

審査員がデモ PR の検証を再実行できるが、無制限には走らせない:
- 回数: `demo_rerun:` トリガーの run を日次で数え、上限（settings.demo_rerun_limit_per_day）で拒否
- 費用: 実行自体は費用ガード（CostGuard）が守る。枠を超えていればエージェント調査を行わず
  UNKNOWN のままゲート失敗になるので、その旨を事前に返して UI に表示する
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.models import Project
from speclino.db.repositories import ReviewRunRepository
from speclino.services.cost_guard import CostGuard
from speclino.services.review_run_service import ReviewRunService

TRIGGER_PREFIX = "demo_rerun:"


@dataclass(frozen=True)
class RerunOutcome:
    run_id: UUID | None  # None なら拒否
    used_today: int
    limit: int
    reason: str
    cost_ok: bool
    cost_note: str

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used_today, 0)


class RerunNotFound(LookupError):
    """元の run が無い。"""


async def demo_rerun(
    session_factory: async_sessionmaker[AsyncSession],
    service: ReviewRunService,
    cost_guard: CostGuard | None,
    run_id: UUID,
    user: str,
    limit_per_day: int,
    now: datetime | None = None,
) -> RerunOutcome:
    now = now or datetime.now(UTC)
    day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
    async with session_factory() as session:
        runs = ReviewRunRepository(session)
        original = await runs.get(run_id)
        if original is None:
            raise RerunNotFound(str(run_id))
        project = await session.get(Project, original.project_id)
        assert project is not None
        used = await runs.count_since(TRIGGER_PREFIX, day_start)
    cost_ok, cost_note = (
        await cost_guard.can_start(now.date())
        if cost_guard is not None
        else (True, "費用ガードなし")
    )
    if used >= limit_per_day:
        return RerunOutcome(
            None,
            used,
            limit_per_day,
            f"本日の再実行上限 {limit_per_day} 回に達した",
            cost_ok,
            cost_note,
        )
    new_id = await service.create_run(
        repo_full_name=project.repo_full_name,
        head_sha=original.head_sha,
        base_sha=original.base_sha,
        pr_number=original.pr_number,
        trigger=f"{TRIGGER_PREFIX}{user}",
        installation_id=project.installation_id,
        default_branch=project.default_branch,
    )
    return RerunOutcome(new_id, used + 1, limit_per_day, "再実行を開始した", cost_ok, cost_note)


__all__ = ["TRIGGER_PREFIX", "RerunNotFound", "RerunOutcome", "demo_rerun"]
