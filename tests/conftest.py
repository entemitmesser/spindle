from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.config import ConfigStore
from spindle.main import create_app
from spindle.services.events import EventBus


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SPINDLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPINDLE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SPINDLE_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SPINDLE_DEV_MODE", "true")
    # Alembic is exercised directly in test_alembic.py; skip it here for speed
    # and isolation and create the schema straight from the models instead.
    monkeypatch.setenv("SPINDLE_RUN_MIGRATIONS", "false")
    # The Dispatcher/DriveWatcher poll immediately on startup (unlike
    # CleanupService, which sleeps first) — leaving them on would race
    # every test that creates its own Job/Drive rows. Tests that want the
    # real background wiring set this env var themselves.
    monkeypatch.setenv("SPINDLE_RUN_BACKGROUND_WORKERS", "false")
    yield create_app()


@pytest_asyncio.fixture
async def running_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(running_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=running_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def session_factory(running_app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return running_app.state.session_factory


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
def event_bus(running_app: FastAPI) -> EventBus:
    return running_app.state.event_bus


@pytest.fixture
def config_store(running_app: FastAPI) -> ConfigStore:
    return running_app.state.config_store
