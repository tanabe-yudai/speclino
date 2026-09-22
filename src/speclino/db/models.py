"""SQLAlchemy の宣言的ベースクラス。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """全 ORM モデルの共通ベース。"""
