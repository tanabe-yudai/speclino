"""`policy_revisions` の repository（S42）。

チーム規約の改訂は不変・連番で、承認されるまで有効にならない
（design/2026-09-26-team-policies-and-dx.md 3章）。監査ログは書かない
（`services/policy_governance.py` が repository と audit をまとめて呼ぶ）。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import PolicyRevisionRecord
from speclino.domain.policy import PolicyPackage, PolicyRevision

_MAX_CREATE_DRAFT_ATTEMPTS = 5


def _to_domain(row: PolicyRevisionRecord) -> PolicyRevision:
    return PolicyRevision(
        team=row.team,
        revision=row.revision,
        status=row.status,
        package=PolicyPackage.model_validate(row.package),
        author=row.author,
        approver=row.approver,
        note=row.note,
    )


class PolicyRevisionRepository:
    """`policy_revisions` テーブルの読み書きを担う。返り値は DB 行ではなく domain の
    `PolicyRevision`（pydantic）に変換する（`created_at` / `approved_at` は domain 型に
    無いため、行から domain へは一方向。行そのものが要る呼び出し側は使わない想定）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_revision(self, team: str) -> PolicyRevision | None:
        """team の現在の active 改訂。部分一意インデックスにより高々 1 件。"""
        stmt = select(PolicyRevisionRecord).where(
            PolicyRevisionRecord.team == team, PolicyRevisionRecord.status == "active"
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def revision_at(self, team: str, revision: int) -> PolicyRevision | None:
        """特定の改訂番号を取得する（replay 用。改訂は不変なので過去の番号も残る）。"""
        stmt = select(PolicyRevisionRecord).where(
            PolicyRevisionRecord.team == team, PolicyRevisionRecord.revision == revision
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def list_revisions(self, team: str) -> list[PolicyRevision]:
        """team の改訂を新しい順に返す。"""
        stmt = (
            select(PolicyRevisionRecord)
            .where(PolicyRevisionRecord.team == team)
            .order_by(PolicyRevisionRecord.revision.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def create_draft(
        self,
        *,
        team: str,
        package: PolicyPackage,
        author: str,
        note: str = "",
    ) -> PolicyRevision:
        """draft を revision=max+1 で作る。

        採番方式: 「max を読んで +1 して INSERT」は読み取りと書き込みの間に競合の
        余地がある（2つの draft 作成が同時に同じ次番号を計算しうる）。advisory lock
        等で直列化する案もあるが、draft 作成は人間が UI で行う低頻度の操作なので、
        (team, revision) の UNIQUE 制約に最終的な正しさを委ね、制約違反
        （`IntegrityError`）を検知したら SAVEPOINT だけ巻き戻して max を再計算し
        retry する方式にした（呼び出し側の外側トランザクションは壊さない）。
        """
        for attempt in range(_MAX_CREATE_DRAFT_ATTEMPTS):
            next_revision = await self._next_revision(team)
            row = PolicyRevisionRecord(
                team=team,
                revision=next_revision,
                status="draft",
                package=package.model_dump(mode="json"),
                author=author,
                approver=None,
                note=note,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(row)
                    await self._session.flush()
            except IntegrityError:
                if attempt == _MAX_CREATE_DRAFT_ATTEMPTS - 1:
                    raise
                continue
            return _to_domain(row)
        raise AssertionError("unreachable")  # ループは return か raise で必ず抜ける

    async def _next_revision(self, team: str) -> int:
        stmt = select(func.coalesce(func.max(PolicyRevisionRecord.revision), 0)).where(
            PolicyRevisionRecord.team == team
        )
        current_max = (await self._session.execute(stmt)).scalar_one()
        return int(current_max) + 1

    async def approve(self, *, team: str, revision: int, approver: str) -> PolicyRevision:
        """draft を承認する。既存の active を superseded にし、自分が active になる。

        2つの UPDATE は同じ `session` 上で commit を挟まずに実行するため、呼び出し側が
        1つのトランザクションとして commit/rollback すれば原子的になる（このリポジトリ
        層は他のリポジトリと同様、自分でトランザクション境界を開かない）。
        """
        stmt = select(PolicyRevisionRecord).where(
            PolicyRevisionRecord.team == team, PolicyRevisionRecord.revision == revision
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValueError(f"policy revision not found: {team}@{revision}")
        if row.status != "draft":
            raise ValueError(f"draft でない改訂は承認できない: {team}@{revision} は {row.status}")

        await self._session.execute(
            update(PolicyRevisionRecord)
            .where(PolicyRevisionRecord.team == team, PolicyRevisionRecord.status == "active")
            .values(status="superseded")
        )
        await self._session.execute(
            update(PolicyRevisionRecord)
            .where(PolicyRevisionRecord.id == row.id)
            .values(status="active", approver=approver, approved_at=func.now())
        )
        # bulk UPDATE は ORM の識別マップを更新しないため、明示的に refresh する
        # （tests/integration/db/test_repositories.py の既存の注記と同じ理由）。
        await self._session.flush()
        await self._session.refresh(row)
        return _to_domain(row)


__all__ = ["PolicyRevisionRepository"]
