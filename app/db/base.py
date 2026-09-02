"""Підключення до БД: engine, фабрика сесій, ініціалізація схеми."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.db_url, echo=False, future=True)

        # SQLite за замовчуванням ігнорує FOREIGN KEY -> вмикаємо явно,
        # інакше ON DELETE CASCADE не спрацює.
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_db() -> None:
    """Доводить схему до останньої ревізії й оновлює службові view.

    Схему веде alembic, а не `create_all`: наявні таблиці той не міняє, тож
    будь-яка правка моделей проходила б повз базу непомітно. Подробиці й
    випадок бази зі спадщини — в `app.db.migrate`.
    """
    from app.db import models  # noqa: F401  — щоб моделі зареєструвались у метаданих
    from app.db.migrate import upgrade_database
    from app.db.views import create_views

    # alembic працює синхронним драйвером, тож не блокуємо цикл подій
    await asyncio.to_thread(upgrade_database)

    engine = get_engine()
    async with engine.begin() as conn:
        await create_views(conn)


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сесія з автоматичним commit/rollback."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-залежність."""
    async with session_scope() as session:
        yield session
