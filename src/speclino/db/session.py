"""非同期 DB エンジン/セッションの生成。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from speclino.settings import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    """設定から非同期エンジンを生成する。"""
    return create_async_engine(settings.database_url)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """エンジンから非同期セッションファクトリを生成する。"""
    return async_sessionmaker(engine, expire_on_commit=False)
