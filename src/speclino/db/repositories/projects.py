"""`projects` の repository（S14）。"""

from __future__ import annotations

import uuid

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project


class ProjectRepository:
    """`projects` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_by_repo(self, repo_full_name: str) -> Project | None:
        stmt = select(Project).where(Project.repo_full_name == repo_full_name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_github_id(self, github_repo_id: int) -> Project | None:
        stmt = select(Project).where(Project.github_repo_id == github_repo_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def resolve_for_webhook(
        self,
        *,
        github_repo_id: int,
        repo_full_name: str,
        installation_id: int | None,
        default_branch: str = "main",
    ) -> tuple[Project, list[str]]:
        """webhook のリポジトリ（ID と今の名前）に対応する project を返す（S53）。

        同一性は ID で決める。名前は改名後に別リポジトリへ再利用されうるので、名前が一致する
        だけの既存行は同じリポジトリとみなさない（その行の team・承認者・過去の run を引き継が
        せない）。
        - ID で見つかる → その行。名前が変わっていれば追随する
        - ID で見つからない → 新しい行（未登録＝team NULL）を作る
        どちらの場合も、今の名前を別の行が使っていれば、その行を `<name>#stale-<id 先頭>` に
        退避して team を外す（ID の無い S53 以前の行も含む。ID の backfill は管理 CLI で行う）。
        戻り値の 2 つめは退避した行の元の名前（監査用）。

        同じリポジトリの webhook が同時に来ても 1 行にまとまるよう、作成は ID の部分一意
        インデックスに対する `INSERT ... ON CONFLICT DO UPDATE` で行う。`settings`（承認者など）
        は書き換えない（S52）。
        """
        evicted = await self._evict_name(repo_full_name, keep_github_repo_id=github_repo_id)
        stmt = (
            pg_insert(Project)
            .values(
                repo_full_name=repo_full_name,
                github_repo_id=github_repo_id,
                installation_id=installation_id,
                default_branch=default_branch,
                settings={},
            )
            .on_conflict_do_update(
                index_elements=[Project.github_repo_id],
                index_where=Project.github_repo_id.isnot(None),
                set_={
                    "repo_full_name": repo_full_name,
                    "installation_id": func.coalesce(installation_id, Project.installation_id),
                    "updated_at": func.now(),
                },
            )
            .returning(Project.id)
        )
        project_id = (await self._session.execute(stmt)).scalar_one()
        project = await self._session.get(Project, project_id, populate_existing=True)
        assert project is not None  # upsert 直後なので必ず存在する
        return project, evicted

    async def _evict_name(self, repo_full_name: str, *, keep_github_repo_id: int) -> list[str]:
        """repo_full_name を使っている別リポジトリの行を退避し、登録（team）を外す。"""
        stmt = (
            update(Project)
            .where(
                Project.repo_full_name == repo_full_name,
                or_(
                    Project.github_repo_id.is_(None),
                    Project.github_repo_id != keep_github_repo_id,
                ),
            )
            .values(
                repo_full_name=Project.repo_full_name
                + "#stale-"
                + func.left(cast(Project.id, String), 8),
                team=None,
                updated_at=func.now(),
            )
            .returning(Project.repo_full_name)
        )
        renamed = (await self._session.execute(stmt)).scalars().all()
        return [name.split("#stale-", 1)[0] for name in renamed]
