from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.adapters.optical import DiscStatus, DriveInfo, DvdTitleInfo, TrayStatus, VolumeInfo
from spindle.config import ConfigStore
from spindle.models.disc import Disc
from spindle.models.drive import Drive, DriveStatus
from spindle.models.event import Event
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.services.detector import DriveWatcher
from spindle.services.events import EventBus

_DEV = "/dev/sr0"


class _FakeAdapter:
    """Duck-typed stand-in for `OpticalAdapter`, per the phase's own guidance:
    'with a fake /sys/block and a stubbed OpticalAdapter'.
    """

    def __init__(self) -> None:
        self.tray = TrayStatus.NO_DISC
        self.disc_status_value = DiscStatus.DATA
        self.root_entries: list[str] = ["VIDEO_TS", "AUDIO_TS"]
        self.size_bytes = 4 * 1024**3
        self.volume_id = "INCEPTION"
        self.dvd_titles = [DvdTitleInfo(index=1, length_seconds=120 * 60)]

    def enumerate_drives(self) -> list[DriveInfo]:
        return [DriveInfo(device_path=_DEV, model="Fake Drive", vendor="Fake")]

    def drive_status(self, dev: str) -> TrayStatus:
        return self.tray

    def disc_status(self, dev: str) -> DiscStatus:
        return self.disc_status_value

    def disc_size_bytes(self, dev: str) -> int | None:
        return self.size_bytes

    def read_volume_info(self, dev: str) -> VolumeInfo:
        return VolumeInfo(volume_id=self.volume_id)

    def list_root_entries(self, dev: str) -> list[str]:
        return self.root_entries

    def list_entries(self, dev: str, path: object) -> list[Any]:
        return []

    def read_dvd_title_info(self, dev: str) -> list[DvdTitleInfo]:
        return self.dvd_titles


class _FakeTmdb:
    def __init__(self, movie_results: list[dict[str, Any]]) -> None:
        self.movie_results = movie_results
        self.movie_calls = 0
        self.tv_calls = 0

    async def search_movie(self, query: str, *, year: int | None = None) -> list[dict[str, Any]]:
        self.movie_calls += 1
        return self.movie_results

    async def search_tv(self, query: str, *, year: int | None = None) -> list[dict[str, Any]]:
        self.tv_calls += 1
        return []


_INCEPTION_RESULT = [
    {
        "id": 27205,
        "title": "Inception",
        "original_title": "Inception",
        "release_date": "2010-07-16",
        "popularity": 95.0,
        "poster_path": "/x.jpg",
    }
]


@pytest.fixture
def fake_adapter() -> _FakeAdapter:
    return _FakeAdapter()


@pytest.fixture
def fake_tmdb() -> _FakeTmdb:
    return _FakeTmdb(_INCEPTION_RESULT)


@pytest.fixture
def watcher(
    fake_adapter: _FakeAdapter,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    fake_tmdb: _FakeTmdb,
) -> DriveWatcher:
    return DriveWatcher(
        fake_adapter,  # type: ignore[arg-type]
        session_factory,
        event_bus,
        config_store,
        fake_tmdb,  # type: ignore[arg-type]
        poll_interval=1000.0,
        debounce_seconds=0.1,
    )


async def _get_drive(session_factory: async_sessionmaker[AsyncSession]) -> Drive:
    async with session_factory() as session:
        result = await session.execute(select(Drive).where(Drive.device_path == _DEV))
        return result.scalar_one()


async def _events(
    session_factory: async_sessionmaker[AsyncSession], event_type: str
) -> list[Event]:
    async with session_factory() as session:
        result = await session.execute(select(Event).where(Event.type == event_type))
        return list(result.scalars().all())


class TestDriveLifecycle:
    async def test_first_poll_creates_drive_row_empty(
        self, watcher: DriveWatcher, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await watcher.poll_once()
        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.EMPTY
        assert drive.device_path == _DEV

    async def test_disc_ready_requires_debounce_before_job_created(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await watcher.poll_once()  # drive row exists, empty
        fake_adapter.tray = TrayStatus.DISC_OK

        await watcher.poll_once()  # first "ready" observation
        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.LOADING

        async with session_factory() as session:
            assert (await session.execute(select(Disc))).scalar_one_or_none() is None

        await watcher.poll_once()  # too soon — debounce not satisfied yet
        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.LOADING

        await asyncio.sleep(0.15)
        await watcher.poll_once()  # debounce satisfied
        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.READY
        assert drive.current_job_id is not None

    async def test_insert_creates_disc_and_identified_job(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        fake_tmdb: _FakeTmdb,
    ) -> None:
        await watcher.poll_once()
        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()

        drive = await _get_drive(session_factory)
        async with session_factory() as session:
            job = await session.get(Job, drive.current_job_id)
        assert job is not None
        assert job.title == "Inception"
        assert job.tmdb_id == 27205
        assert job.status == JobStatus.QUEUED
        assert fake_tmdb.movie_calls == 1

        inserted_events = await _events(session_factory, "drive.disc_inserted")
        created_events = await _events(session_factory, "job.created")
        assert len(inserted_events) == 1
        assert len(created_events) == 1

    async def test_removal_clears_drive_and_emits_event(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await watcher.poll_once()
        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()

        fake_adapter.tray = TrayStatus.NO_DISC
        await watcher.poll_once()

        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.EMPTY
        assert drive.current_job_id is None

        ejected_events = await _events(session_factory, "drive.disc_ejected")
        assert len(ejected_events) == 1

    async def test_reinsert_same_disc_reuses_fingerprint_and_skips_tmdb(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        fake_tmdb: _FakeTmdb,
    ) -> None:
        await watcher.poll_once()
        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()
        assert fake_tmdb.movie_calls == 1

        fake_adapter.tray = TrayStatus.NO_DISC
        await watcher.poll_once()

        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()

        drive = await _get_drive(session_factory)
        async with session_factory() as session:
            job = await session.get(Job, drive.current_job_id)

        assert job is not None
        assert job.title == "Inception"
        assert job.tmdb_id == 27205
        assert job.confidence == 1.0
        assert fake_tmdb.movie_calls == 1  # not called a second time

    async def test_auto_start_disabled_creates_disc_without_job(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        config_store: ConfigStore,
    ) -> None:
        await config_store.update({"behaviour": {"auto_start": False}})

        await watcher.poll_once()
        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()

        drive = await _get_drive(session_factory)
        assert drive.status == DriveStatus.READY
        assert drive.current_job_id is None

        async with session_factory() as session:
            disc = (await session.execute(select(Disc))).scalar_one()
        assert disc.disc_type is not None


class TestRunLoopResilience:
    async def test_a_failing_poll_does_not_kill_the_watcher(
        self,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config_store: ConfigStore,
        fake_tmdb: _FakeTmdb,
    ) -> None:
        """Regression test: `run()`'s loop used to have no try/except around
        `poll_once()`, so any unexpected exception (e.g. a permission error
        surfacing from the adapter) silently killed disc detection for the
        rest of the process's life -- with nothing in the logs, since the
        task's exception is only surfaced by asyncio once the task object is
        garbage collected, and `main.py` holds a reference to it until
        shutdown. It must instead log, emit `system.warning`, and keep
        polling on the next interval."""
        watcher = DriveWatcher(
            fake_adapter,  # type: ignore[arg-type]
            session_factory,
            event_bus,
            config_store,
            fake_tmdb,  # type: ignore[arg-type]
            poll_interval=0.05,
            debounce_seconds=0.05,
        )

        call_count = 0
        real_poll_once = watcher.poll_once

        async def flaky_poll_once() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated adapter failure")
            await real_poll_once()

        watcher.poll_once = flaky_poll_once  # type: ignore[method-assign]

        task = asyncio.create_task(watcher.run())
        try:
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert call_count >= 2  # survived the first failure and kept polling

        async with session_factory() as session:
            result = await session.execute(select(Event).where(Event.type == "system.warning"))
            warnings = list(result.scalars().all())
        assert any(w.data.get("code") == "drive_poll_failed" for w in warnings)


class TestAudioCd:
    async def test_audio_cd_insert_parks_needs_review_without_calling_tmdb(
        self,
        watcher: DriveWatcher,
        fake_adapter: _FakeAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        fake_tmdb: _FakeTmdb,
    ) -> None:
        fake_adapter.disc_status_value = DiscStatus.AUDIO

        await watcher.poll_once()
        fake_adapter.tray = TrayStatus.DISC_OK
        await watcher.poll_once()
        await asyncio.sleep(0.15)
        await watcher.poll_once()

        drive = await _get_drive(session_factory)
        async with session_factory() as session:
            job = await session.get(Job, drive.current_job_id)

        assert job is not None
        assert job.status == JobStatus.NEEDS_REVIEW
        assert job.media_kind == MediaKind.MUSIC
        assert job.error == "Audio CDs aren't supported yet."
        assert fake_tmdb.movie_calls == 0
        assert fake_tmdb.tv_calls == 0
