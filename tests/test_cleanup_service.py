from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.config import ConfigStore
from spindle.models.event import Event
from spindle.models.job import JobStatus
from spindle.models.types import utc_now
from spindle.services.cleanup import CleanupService
from spindle.services.events import EventBus
from spindle.utils import fsutil
from tests.factories import make_job


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> CleanupService:
    return CleanupService(
        session_factory,
        event_bus,
        config_store,
        work_dir=tmp_path / "work",
        library_dir=tmp_path / "library",
    )


async def test_deletes_work_dirs_for_jobs_completed_past_retention(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    await config_store.update({"behaviour": {"completed_retention_days": 7}})
    job = await make_job(
        session,
        status=JobStatus.COMPLETED,
        finished_at=utc_now() - timedelta(days=10),
    )
    raw = fsutil.raw_dir(tmp_path / "work", job.id)
    transcode = fsutil.transcode_dir(tmp_path / "work", job.id)
    raw.mkdir(parents=True)
    transcode.mkdir(parents=True)
    (transcode / "title.mkv").write_bytes(b"x")

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert not raw.exists()
    assert not transcode.exists()


async def test_keeps_work_dirs_for_recently_completed_jobs(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    await config_store.update({"behaviour": {"completed_retention_days": 7}})
    job = await make_job(
        session,
        status=JobStatus.COMPLETED,
        finished_at=utc_now() - timedelta(days=1),
    )
    transcode = fsutil.transcode_dir(tmp_path / "work", job.id)
    transcode.mkdir(parents=True)

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert transcode.exists()


async def test_keeps_work_dirs_for_jobs_still_in_progress(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    await config_store.update({"behaviour": {"completed_retention_days": 0}})
    job = await make_job(session, status=JobStatus.RUNNING)
    transcode = fsutil.transcode_dir(tmp_path / "work", job.id)
    transcode.mkdir(parents=True)

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert transcode.exists()


async def test_deletes_orphaned_work_dirs_with_no_matching_job_row(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    orphan = fsutil.transcode_dir(tmp_path / "work", 999_999)
    orphan.mkdir(parents=True)

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert not orphan.exists()


async def test_keeps_work_dirs_that_match_a_known_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    job = await make_job(session, status=JobStatus.QUEUED)
    transcode = fsutil.transcode_dir(tmp_path / "work", job.id)
    transcode.mkdir(parents=True)

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert transcode.exists()


async def test_ignores_non_job_directories_under_work(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    stray = tmp_path / "work" / "transcode" / "not-a-job-dir"
    stray.mkdir(parents=True)

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    assert stray.exists()


class _FakeUsage(NamedTuple):
    total: int
    used: int
    free: int


async def test_emits_a_system_warning_when_free_space_is_low(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "library").mkdir(exist_ok=True)
    await config_store.update({"behaviour": {"min_free_bytes": 100 * 1024**3}})
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _FakeUsage(0, 0, 1024))

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    async with session_factory() as s:
        result = await s.execute(select(Event).where(col(Event.type) == "system.warning"))
        warnings = result.scalars().all()
    assert any(e.data.get("code") == "low_disk_space" for e in warnings)


async def test_no_warning_when_free_space_is_fine(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "library").mkdir(exist_ok=True)
    await config_store.update({"behaviour": {"min_free_bytes": 1024}})
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _FakeUsage(0, 0, 100 * 1024**3))

    service = _service(session_factory, event_bus, config_store, tmp_path)
    await service.run_once()

    async with session_factory() as s:
        result = await s.execute(select(Event).where(col(Event.type) == "system.warning"))
        warnings = result.scalars().all()
    assert not any(e.data.get("code") == "low_disk_space" for e in warnings)


async def test_run_once_trims_the_event_table(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    calls = 0
    original = event_bus.trim_retention

    async def _tracked() -> None:
        nonlocal calls
        calls += 1
        await original()

    event_bus.trim_retention = _tracked  # type: ignore[method-assign]
    service = _service(session_factory, event_bus, config_store, tmp_path)

    await service.run_once()

    assert calls == 1
