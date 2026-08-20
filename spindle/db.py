from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Request
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from spindle.config import Settings

ALEMBIC_INI_PATH = Path(__file__).resolve().parent.parent / "alembic.ini"


def database_url(settings: Settings) -> str:
    return f"sqlite+aiosqlite:///{settings.data_dir / 'spindle.db'}"


def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(database_url(settings))
    sa_event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables(engine: AsyncEngine) -> None:
    """Fallback schema creation for contexts that skip Alembic (tests)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


def _upgrade_to_head_sync() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ALEMBIC_INI_PATH)), "head")


async def init_schema(engine: AsyncEngine, settings: Settings) -> None:
    """Bring the schema up to date: Alembic in production, `create_all` in tests.

    Alembic's `command.upgrade` is a blocking sync call against its own engine,
    so it runs in a thread rather than on the event loop.
    """
    if settings.run_migrations:
        await asyncio.to_thread(_upgrade_to_head_sync)
    else:
        await create_tables(engine)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
