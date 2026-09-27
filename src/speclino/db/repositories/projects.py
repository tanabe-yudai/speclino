"""`projects` の repository（S14）。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
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

    async def upsert(
        self,
        *,
        repo_full_name: str,
        installation_id: int | None,
        default_branch: str,
    ) -> Project:
        """`repo_full_name` の一意制約に対する `INSERT ... ON CONFLICT DO UPDATE`。

        GitHub App のインストール/再インストールで同じリポジトリの upsert が競合しても
        原子的に解決できるよう、get→insert/update の 2 ステップではなく単一文にする。

        `settings`（承認者など）は run の作成で書き換えない。以前は呼ぶたびに `{}` で上書きし、
        project の承認者を消していた（S52）。`installation_id` も、MCP などインストールを
        知らない経路（None）から呼ばれたときは既存値を保つ。
        """
        stmt = (
            pg_insert(Project)
            .values(
                repo_full_name=repo_full_name,
                installation_id=installation_id,
                default_branch=default_branch,
                settings={},
            )
            .on_conflict_do_update(
                index_elements=[Project.repo_full_name],
                set_={
                    "installation_id": func.coalesce(installation_id, Project.installation_id),
                    "default_branch": default_branch,
                    "updated_at": func.now(),
                },
            )
            .returning(Project.id)
        )
        result = await self._session.execute(stmt)
        project_id = result.scalar_one()
        project = await self._session.get(Project, project_id)
        assert project is not None  # upsert 直後なので必ず存在する
        return project
