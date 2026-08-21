"""Periodic housekeeping, per phase-06-library-jellyfin.md task 5.

Runs hourly: deletes `work/` directories for jobs that have been `completed`
for longer than `behaviour.completed_retention_days`, deletes orphaned work
directories with no matching job row, trims the `event` table (via
`EventBus.trim_retention`, folded in here so there's one periodic
maintenance task rather than two independently-scheduled ones), and
recomputes disk-space warnings for the work/library volumes.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.config import ConfigStore
from spindle.models.job import Job, JobStatus
from spindle.models.types import utc_now
from spindle.services.events import EventBus, EventIn
from spindle.utils import fsutil

logger = structlog.get_logger(__name__)

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DISK_WATCH_INTERVAL_SECONDS = 60.0
_WORK_SUBDIRS = ("raw", "transcode")


def _parse_job_dir_name(name: str) -> int | None:
    if not name.startswith("job-"):
        return None
    try:
        return int(name[len("job-") :])
    except ValueError:
        return None


class CleanupService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config_store: ConfigStore,
        *,
        work_dir: Path,
        library_dir: Path,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._config_store = config_store
        self._work_dir = work_dir
        self._library_dir = library_dir

    async def run_once(self) -> None:
        config = await self._config_store.get()
        await self._delete_expired_job_work_dirs(config.behaviour.completed_retention_days)
        await self._delete_orphaned_work_dirs()
        await self._event_bus.trim_retention()
        await self._check_disk_space(config.behaviour.min_free_bytes)

    async def run_loop(self, *, interval_seconds: float = _DEFAULT_INTERVAL_SECONDS) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 -- cleanup must never kill the loop
                logger.exception("cleanup.run_failed")

    async def run_disk_watch_loop(
        self, *, interval_seconds: float = _DISK_WATCH_INTERVAL_SECONDS
    ) -> None:
        """The disk-space monitor task from phase-09-hardening.md task 2, on
        its own faster cadence -- `run_loop`'s hourly sweep also checks disk
        space (`run_once` -> `_check_disk_space`), but a full-volume disk
        should surface a warning well inside an hour."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                config = await self._config_store.get()
                await self._check_disk_space(config.behaviour.min_free_bytes)
            except Exception:  # noqa: BLE001 -- must never kill the loop
                logger.exception("cleanup.disk_watch_failed")

    # -- internals -----------------------------------------------------

    async def _delete_expired_job_work_dirs(self, retention_days: int) -> None:
        cutoff = utc_now() - timedelta(days=retention_days)
        async with self._session_factory() as session:
            result = await session.execute(
                select(Job).where(
                    col(Job.status) == JobStatus.COMPLETED,
                    col(Job.finished_at).is_not(None),
                    col(Job.finished_at) < cutoff,
                )
            )
            jobs = list(result.scalars().all())

        for job in jobs:
            if job.id is None:
                continue
            for subdir in _WORK_SUBDIRS:
                path = self._work_dir / subdir / fsutil.job_dir_name(job.id)
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)

    async def _delete_orphaned_work_dirs(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(col(Job.id)))
            known_ids = set(result.scalars().all())

        for subdir in _WORK_SUBDIRS:
            root = self._work_dir / subdir
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                job_id = _parse_job_dir_name(entry.name)
                if job_id is not None and job_id not in known_ids:
                    await asyncio.to_thread(shutil.rmtree, entry, ignore_errors=True)

    async def _check_disk_space(self, min_free_bytes: int) -> None:
        for label, path in (("work", self._work_dir), ("library", self._library_dir)):
            if not path.is_dir():
                continue
            usage = await asyncio.to_thread(shutil.disk_usage, path)
            if usage.free < min_free_bytes:
                await self._event_bus.emit(
                    EventIn(
                        type="system.warning",
                        data={
                            "code": "low_disk_space",
                            "message": f"Low disk space on the {label} volume.",
                        },
                    )
                )
