"""`DriveWatcher`: udev-accelerated, poll-driven disc detection.

udev events are unreliable in containers (missing `/run/udev`, permissions),
so the 5s poll is the source of truth and udev is only ever used to trigger
an early poll — see the phase 2 gotchas in `docs/phases/phase-02-detection.md`.
`poll_once()` is deliberately public and side-effect-complete (one full
enumerate-and-react pass) so tests can drive the state machine deterministically
without running the infinite loop or a real udev monitor.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.adapters.optical import DriveInfo, OpticalAdapter, TrayStatus
from spindle.adapters.tmdb import TmdbAdapter
from spindle.config import ConfigStore
from spindle.models.disc import Disc, DiscType
from spindle.models.drive import Drive, DriveStatus
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.services.events import EventBus, EventIn
from spindle.services.identifier import classify, compute_fingerprint, identify_disc
from spindle.services.repo import get_latest_job_by_fingerprint
from spindle.utils.naming import DiscLabelHints, normalize_disc_label

logger = structlog.get_logger(__name__)

_DEBOUNCE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 5.0
_MINUTES_PER_SECOND = 1 / 60.0


def _map_idle_tray_status(tray: TrayStatus) -> DriveStatus:
    if tray == TrayStatus.NOT_READY:
        return DriveStatus.LOADING
    if tray == TrayStatus.UNKNOWN:
        return DriveStatus.ERROR
    return DriveStatus.EMPTY  # NO_DISC or TRAY_OPEN


class DriveWatcher:
    def __init__(
        self,
        adapter: OpticalAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config_store: ConfigStore,
        tmdb: TmdbAdapter,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        debounce_seconds: float = _DEBOUNCE_SECONDS,
    ) -> None:
        self._adapter = adapter
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._config_store = config_store
        self._tmdb = tmdb
        self._poll_interval = poll_interval
        self._debounce_seconds = debounce_seconds

        self._wake = asyncio.Event()
        self._pending_ready: dict[str, float] = {}
        self._udev_observer: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._start_udev_observer()
        try:
            while True:
                await self.poll_once()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
                self._wake.clear()
        finally:
            self._stop_udev_observer()

    async def poll_once(self) -> None:
        drive_infos = await asyncio.to_thread(self._adapter.enumerate_drives)
        async with self._session_factory() as session:
            for info in drive_infos:
                drive = await self._get_or_create_drive(session, info)
                await self._poll_drive(session, drive)

    # -- drive/disc lifecycle -----------------------------------------------

    async def _get_or_create_drive(self, session: AsyncSession, info: DriveInfo) -> Drive:
        result = await session.execute(
            select(Drive).where(col(Drive.device_path) == info.device_path)
        )
        drive = result.scalar_one_or_none()
        if drive is None:
            drive = Drive(
                device_path=info.device_path,
                model=info.model,
                vendor=info.vendor,
                status=DriveStatus.EMPTY,
            )
            session.add(drive)
            await session.commit()
            await session.refresh(drive)
        return drive

    async def _poll_drive(self, session: AsyncSession, drive: Drive) -> None:
        if not drive.is_enabled:
            return

        tray = await asyncio.to_thread(self._adapter.drive_status, drive.device_path)

        if tray != TrayStatus.DISC_OK:
            self._pending_ready.pop(drive.device_path, None)
            if drive.status == DriveStatus.READY and tray in (
                TrayStatus.NO_DISC,
                TrayStatus.TRAY_OPEN,
            ):
                await self._handle_removal(session, drive)
            else:
                drive.status = _map_idle_tray_status(tray)
                drive.last_seen_at = utc_now()
                await session.commit()
            return

        if drive.status == DriveStatus.READY:
            return  # already have a disc/job for this drive

        first_seen = self._pending_ready.get(drive.device_path)
        now = time.monotonic()
        if first_seen is None:
            self._pending_ready[drive.device_path] = now
            drive.status = DriveStatus.LOADING
            drive.last_seen_at = utc_now()
            await session.commit()
            return
        if now - first_seen < self._debounce_seconds:
            return

        self._pending_ready.pop(drive.device_path, None)
        await self._handle_insert(session, drive)

    async def _handle_insert(self, session: AsyncSession, drive: Drive) -> None:
        dev = drive.device_path
        volume = await asyncio.to_thread(self._adapter.read_volume_info, dev)
        disc_status = await asyncio.to_thread(self._adapter.disc_status, dev)
        root_entries = await asyncio.to_thread(self._adapter.list_root_entries, dev)
        size_bytes = await asyncio.to_thread(self._adapter.disc_size_bytes, dev)
        disc_type = classify(
            disc_status=disc_status, root_entries=root_entries, size_bytes=size_bytes
        )
        fingerprint = await asyncio.to_thread(compute_fingerprint, self._adapter, dev, disc_type)

        disc = Disc(
            drive_id=drive.id,
            disc_type=disc_type,
            label=volume.volume_id,
            volume_id=volume.volume_id,
            fingerprint=fingerprint,
            size_bytes=size_bytes,
            meta={"disc_status": disc_status.value, "root_entries": root_entries},
        )
        session.add(disc)
        drive.status = DriveStatus.READY
        drive.last_seen_at = utc_now()
        await session.commit()
        await session.refresh(disc)

        await self._event_bus.emit(
            EventIn(
                type="drive.disc_inserted",
                drive_id=drive.id,
                data={"disc_type": disc_type.value, "label": volume.volume_id},
            )
        )

        config = await self._config_store.get()
        if not config.behaviour.auto_start:
            return

        job = await self._create_job(session, disc, fingerprint, dev, disc_type)
        drive.current_job_id = job.id
        await session.commit()

    async def _create_job(
        self,
        session: AsyncSession,
        disc: Disc,
        fingerprint: str | None,
        dev: str,
        disc_type: DiscType,
    ) -> Job:
        previous = None
        if fingerprint is not None:
            previous = await get_latest_job_by_fingerprint(
                session, fingerprint, exclude_disc_id=disc.id
            )

        if previous is not None:
            job = Job(
                disc_id=disc.id,
                status=JobStatus.QUEUED,
                media_kind=previous.media_kind,
                title=previous.title,
                original_title=previous.original_title,
                year=previous.year,
                season=previous.season,
                tmdb_id=previous.tmdb_id,
                imdb_id=previous.imdb_id,
                confidence=1.0,
                poster_url=previous.poster_url,
                meta={"fingerprint_reused_from_job_id": previous.id},
            )
        else:
            job = await self._identify_and_build_job(disc, dev, disc_type)

        session.add(job)
        await session.commit()
        await session.refresh(job)

        for kind in TaskKind:
            task_status = (
                TaskStatus.SUCCEEDED
                if kind == TaskKind.IDENTIFY and job.tmdb_id is not None
                else TaskStatus.PENDING
            )
            session.add(Task(job_id=job.id, kind=kind, status=task_status))
        await session.commit()

        await self._event_bus.emit(
            EventIn(
                type="job.created",
                job_id=job.id,
                data={"title": job.title, "media_kind": job.media_kind.value},
            )
        )
        return job

    async def _identify_and_build_job(self, disc: Disc, dev: str, disc_type: DiscType) -> Job:
        normalized_title, hints = normalize_disc_label(disc.label or "")
        disc_runtime_minutes = await asyncio.to_thread(
            self._main_title_runtime_minutes, dev, disc_type
        )
        config = await self._config_store.get()

        if not normalized_title:
            return Job(disc_id=disc.id, status=JobStatus.NEEDS_REVIEW, media_kind=MediaKind.OTHER)

        outcome = await identify_disc(
            self._tmdb,
            normalized_title=normalized_title,
            hints=hints,
            disc_runtime_minutes=disc_runtime_minutes,
            confidence_threshold=config.behaviour.identify_confidence_threshold,
        )

        return Job(
            disc_id=disc.id,
            status=JobStatus.QUEUED if outcome.accepted else JobStatus.NEEDS_REVIEW,
            media_kind=outcome.media_kind,
            title=outcome.title or normalized_title,
            original_title=outcome.original_title,
            year=outcome.year,
            season=hints.season,
            tmdb_id=outcome.tmdb_id,
            confidence=outcome.confidence,
            poster_url=outcome.poster_url,
            meta={"candidates": outcome.candidates, "label_hints": _hints_dict(hints)}
            if outcome.candidates
            else {},
        )

    def _main_title_runtime_minutes(self, dev: str, disc_type: DiscType) -> float | None:
        if disc_type != DiscType.DVD:
            return None  # Blu-ray .mpls playlist duration parsing is out of scope for now
        titles = self._adapter.read_dvd_title_info(dev)
        if not titles:
            return None
        return max(t.length_seconds for t in titles) * _MINUTES_PER_SECOND

    async def _handle_removal(self, session: AsyncSession, drive: Drive) -> None:
        await self._event_bus.emit(EventIn(type="drive.disc_ejected", drive_id=drive.id, data={}))

        if drive.current_job_id is not None:
            job = await session.get(Job, drive.current_job_id)
            if job is not None and job.status == JobStatus.RUNNING:
                result = await session.execute(
                    select(Task).where(col(Task.job_id) == job.id, col(Task.kind) == TaskKind.RIP)
                )
                rip_task = result.scalar_one_or_none()
                if rip_task is not None and rip_task.status == TaskStatus.RUNNING:
                    rip_task.error = "Disc removed during rip."
                    rip_task.status = TaskStatus.FAILED
                    rip_task.finished_at = utc_now()
                    await self._event_bus.emit(
                        EventIn(
                            type="task.failed",
                            job_id=job.id,
                            data={"kind": TaskKind.RIP.value, "error": rip_task.error},
                        )
                    )
            drive.current_job_id = None

        drive.status = DriveStatus.EMPTY
        drive.last_seen_at = utc_now()
        await session.commit()

    # -- udev acceleration (best-effort; poll is the source of truth) ------

    def _start_udev_observer(self) -> None:
        try:
            import pyudev
        except ImportError:
            logger.info("detector.pyudev_unavailable")
            return

        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="block")
            self._udev_observer = pyudev.MonitorObserver(monitor, self._on_udev_event)
            self._udev_observer.start()
        except Exception:  # noqa: BLE001 -- udev is an accelerator, never load-bearing
            logger.warning("detector.udev_start_failed", exc_info=True)
            self._udev_observer = None

    def _on_udev_event(self, action: str, device: Any) -> None:
        if device.get("ID_CDROM") != "1" or device.get("DISK_MEDIA_CHANGE") != "1":
            return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def _stop_udev_observer(self) -> None:
        if self._udev_observer is not None:
            self._udev_observer.stop()
            self._udev_observer = None


def _hints_dict(hints: DiscLabelHints) -> dict[str, Any]:
    return {"disc_number": hints.disc_number, "season": hints.season, "edition": hints.edition}
