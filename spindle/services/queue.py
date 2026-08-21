"""`Dispatcher` + `run_stage`, per architecture.md §3 and
phase-04-transcode-engine.md Part B.

`Dispatcher` implements the scheduling *policy* (what's runnable, what has
capacity) as pure, DB-free functions where possible (`_select_next_task`,
`_is_retry_eligible`) so the decision logic is unit-testable without a
session. `run_stage` owns the transaction boundary stages themselves never
get (per `JobContext`'s docstring and CLAUDE.md rule 3/4): it loads the
job/task/disc/drive, builds the `JobContext`, calls the stage, and persists
whatever the stage decided.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.config import ConfigStore, RuntimeConfig
from spindle.models.disc import Disc
from spindle.models.drive import Drive
from spindle.models.job import Job, JobStatus
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import Stage, StageResult
from spindle.services.events import EventBus, EventIn
from spindle.services.repo import get_active_jobs, get_tasks_for_job
from spindle.utils import fsutil
from spindle.utils.logging import job_logger

_DEFAULT_POLL_INTERVAL: Final = 2.0
_DEFAULT_STOP_TIMEOUT: Final = 30.0

# Task 7: only these kinds auto-retry with backoff; RIP/TRANSCODE never do —
# silently re-ripping/re-encoding is hostile (CLAUDE.md §8).
_AUTO_RETRY_KINDS: Final = frozenset({TaskKind.IDENTIFY, TaskKind.SUBTITLES, TaskKind.NOTIFY})
_RETRY_BACKOFF_BASE_SECONDS: Final = 30

# "Network (metadata/subs): 4" (architecture.md §7 / CLAUDE.md §7). RIP is
# gated per-drive, not by count; ORGANIZE has no documented limit.
_NETWORK_CONCURRENCY: Final = 4
_UNLIMITED: Final = 1_000_000


def _capacity_for_config(config: RuntimeConfig) -> dict[TaskKind, int]:
    return {
        TaskKind.TRANSCODE: max(1, config.transcode.workers),
        TaskKind.IDENTIFY: _NETWORK_CONCURRENCY,
        TaskKind.SUBTITLES: _NETWORK_CONCURRENCY,
        TaskKind.NOTIFY: _NETWORK_CONCURRENCY,
    }


def _is_retry_eligible(task: Task, config: RuntimeConfig, now: datetime) -> bool:
    if task.kind not in _AUTO_RETRY_KINDS:
        return False
    if task.attempts >= config.behaviour.max_retries:
        return False
    if task.finished_at is None:
        return True
    backoff = _RETRY_BACKOFF_BASE_SECONDS * 2**task.attempts
    return bool((now - task.finished_at).total_seconds() >= backoff)


def _select_next_task(tasks: list[Task], config: RuntimeConfig, now: datetime) -> Task | None:
    """The first task (in pipeline order) that isn't done yet. A `RUNNING` or
    non-retry-eligible `FAILED` task blocks everything after it — the
    dispatcher must never skip ahead and run a later stage out of order."""
    for task in tasks:
        if task.status in (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED):
            continue
        if task.status == TaskStatus.PENDING:
            return task
        if task.status == TaskStatus.RUNNING:
            return None
        if task.status == TaskStatus.FAILED:
            return task if _is_retry_eligible(task, config, now) else None
    return None


def _all_terminal_success(tasks: list[Task]) -> bool:
    terminal = (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED)
    return bool(tasks) and all(t.status in terminal for t in tasks)


class Dispatcher:
    def __init__(
        self,
        stages: dict[TaskKind, Stage],
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        config_store: ConfigStore,
        *,
        work_dir: Path,
        data_dir: Path,
        library_dir: Path | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._stages = stages
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._config_store = config_store
        self._work_dir = work_dir
        self._data_dir = data_dir
        self._library_dir = library_dir
        self._poll_interval = poll_interval

        self.wake_event = asyncio.Event()
        self._running: dict[int, asyncio.Task[None]] = {}
        self._running_by_kind: dict[TaskKind, set[int]] = defaultdict(set)
        self._stopping = False

    def running_count(self, kind: TaskKind) -> int:
        return len(self._running_by_kind[kind])

    def cancel_task(self, task_id: int) -> bool:
        running = self._running.get(task_id)
        if running is None:
            return False
        running.cancel()
        return True

    async def run(self) -> None:
        await self.recover_orphaned_tasks()
        while not self._stopping:
            await self.poll_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake_event.wait(), timeout=self._poll_interval)
            self.wake_event.clear()

    async def stop(self, *, grace_period: float = _DEFAULT_STOP_TIMEOUT) -> None:
        self._stopping = True
        running = list(self._running.values())
        for stage_task in running:
            stage_task.cancel()
        if running:
            await asyncio.wait(running, timeout=grace_period)
        for stage_task in running:
            if not stage_task.done():
                stage_task.cancel()

    async def recover_orphaned_tasks(self) -> None:
        """On startup, any task left `running` from a previous process is
        reset to `pending`; its job gets a `system.warning` (task 6)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Task).where(col(Task.status) == TaskStatus.RUNNING)
            )
            orphans = list(result.scalars().all())
            job_ids = {task.job_id for task in orphans}
            for task in orphans:
                task.status = TaskStatus.PENDING
                task.started_at = None
            await session.commit()

        for job_id in job_ids:
            await self._event_bus.emit(
                EventIn(
                    type="system.warning",
                    job_id=job_id,
                    data={
                        "code": "recovered_after_restart",
                        "message": "Recovered an in-progress task after a restart.",
                    },
                )
            )

    async def poll_once(self) -> None:
        config = await self._config_store.get()
        capacity = _capacity_for_config(config)
        now = utc_now()

        async with self._session_factory() as session:
            jobs = await get_active_jobs(session)
            for job in jobs:
                if job.id is None:
                    continue
                tasks = await get_tasks_for_job(session, job.id)
                task = _select_next_task(tasks, config, now)

                if task is None:
                    if _all_terminal_success(tasks):
                        await self._complete_job(session, job)
                    continue

                if task.id is None or task.id in self._running:
                    continue
                if task.kind not in self._stages:
                    continue
                if task.kind == TaskKind.RIP and not await self._drive_available(session, job):
                    continue
                if self.running_count(task.kind) >= capacity.get(task.kind, _UNLIMITED):
                    continue

                self._launch(job.id, task)

    # -- internals -----------------------------------------------------

    async def _drive_available(self, session: AsyncSession, job: Job) -> bool:
        if job.disc_id is None:
            return True
        disc = await session.get(Disc, job.disc_id)
        if disc is None:
            return True
        drive = await session.get(Drive, disc.drive_id)
        if drive is None:
            return True
        return drive.current_job_id in (None, job.id)

    async def _complete_job(self, session: AsyncSession, job: Job) -> None:
        old_status = job.status
        job.status = JobStatus.COMPLETED
        job.finished_at = utc_now()
        job.updated_at = utc_now()
        await session.commit()
        await self._event_bus.emit(
            EventIn(
                type="job.status_changed",
                job_id=job.id,
                data={
                    "from": old_status.value,
                    "to": job.status.value,
                    "reason": "all_tasks_succeeded",
                },
            )
        )

    def _launch(self, job_id: int, task: Task) -> None:
        assert task.id is not None  # noqa: S101 -- checked by the caller
        stage = self._stages[task.kind]
        self._running_by_kind[task.kind].add(task.id)
        self._running[task.id] = asyncio.create_task(
            self._run_and_release(stage, job_id, task.id, task.kind)
        )

    async def _run_and_release(
        self, stage: Stage, job_id: int, task_id: int, kind: TaskKind
    ) -> None:
        try:
            await run_stage(
                stage=stage,
                session_factory=self._session_factory,
                event_bus=self._event_bus,
                config_store=self._config_store,
                work_dir=self._work_dir,
                data_dir=self._data_dir,
                library_dir=self._library_dir,
                job_id=job_id,
                task_id=task_id,
            )
        finally:
            self._running_by_kind[kind].discard(task_id)
            self._running.pop(task_id, None)
            self.wake_event.set()


async def run_stage(
    *,
    stage: Stage,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    work_dir: Path,
    data_dir: Path,
    library_dir: Path | None = None,
    job_id: int,
    task_id: int,
) -> None:
    """Task 8: the only place a stage is ever awaited. Owns statuses, timing,
    log file binding, event emission and DB commits so no stage has to."""
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        task = await session.get(Task, task_id)
        if job is None or task is None:
            return

        disc = await session.get(Disc, job.disc_id) if job.disc_id is not None else None
        drive = await session.get(Drive, disc.drive_id) if disc is not None else None
        drive_id = drive.id if drive is not None else None
        config = await config_store.get()

        async def emit(event_in: EventIn) -> None:
            resolved_drive_id = event_in.drive_id if event_in.drive_id is not None else drive_id
            if event_in.type == "task.progress":
                await _persist_progress(session_factory, task_id, event_in.data)
            await event_bus.emit(
                EventIn(
                    type=event_in.type,
                    data=event_in.data,
                    job_id=job_id,
                    drive_id=resolved_drive_id,
                )
            )

        logger = job_logger(job_id, task.kind.value, data_dir=data_dir)
        log_path = data_dir / "logs" / fsutil.job_dir_name(job_id) / f"{task.kind.value}.log"

        ctx = JobContext(
            job=job,
            disc=disc,
            drive=drive,
            work_dir=work_dir,
            config=config,
            emit=emit,
            logger=logger,
            library_dir=library_dir,
            data_dir=data_dir,
        )

        old_job_status = job.status
        job_started = False
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.RUNNING
            job_started = True
        if job.started_at is None:
            job.started_at = utc_now()

        task.status = TaskStatus.RUNNING
        task.started_at = utc_now()
        task.log_path = str(log_path)
        task.error = None
        await session.commit()

        if job_started:
            await emit(
                EventIn(
                    type="job.status_changed",
                    data={
                        "from": old_job_status.value,
                        "to": job.status.value,
                        "reason": "task_started",
                    },
                )
            )
        await emit(EventIn(type="task.started", data={"kind": task.kind.value}))

        try:
            skip = await stage.can_skip(ctx)
        except Exception:  # noqa: BLE001 -- a broken skip-check must not wedge the task
            logger.exception("stage.can_skip_failed")
            skip = False

        if skip:
            task.status = TaskStatus.SKIPPED
            task.progress = 1.0
            task.finished_at = utc_now()
            await session.commit()
            await emit(
                EventIn(
                    type="task.succeeded",
                    data={"kind": task.kind.value, "duration_seconds": 0, "skipped": True},
                )
            )
            return

        start = time.monotonic()
        try:
            result = await stage.run(ctx)
        except Exception as exc:  # noqa: BLE001 -- any stage failure becomes a failed task
            logger.exception("stage.run_failed")
            with contextlib.suppress(Exception):
                await stage.cleanup(ctx)
            result = StageResult(status=TaskStatus.FAILED, message=str(exc) or type(exc).__name__)

        duration_seconds = int(time.monotonic() - start)
        task.finished_at = utc_now()
        task.message = result.message

        if result.status == TaskStatus.SUCCEEDED:
            task.status = TaskStatus.SUCCEEDED
            task.progress = 1.0
            task.error = None
            await session.commit()
            await emit(
                EventIn(
                    type="task.succeeded",
                    data={"kind": task.kind.value, "duration_seconds": duration_seconds},
                )
            )
            return

        task.attempts += 1
        task.status = TaskStatus.FAILED
        task.error = result.message

        job_failed = False
        old_status = job.status
        if not _is_retry_eligible(task, config, utc_now()):
            job.status = JobStatus.FAILED
            job.error = result.message
            job.finished_at = utc_now()
            job_failed = True
        await session.commit()

        if job_failed:
            await emit(
                EventIn(
                    type="job.status_changed",
                    data={
                        "from": old_status.value,
                        "to": job.status.value,
                        "reason": f"{task.kind.value}_failed",
                    },
                )
            )
        await emit(
            EventIn(
                type="task.failed",
                data={
                    "kind": task.kind.value,
                    "duration_seconds": duration_seconds,
                    "error": result.message,
                },
            )
        )


async def _persist_progress(
    session_factory: async_sessionmaker[AsyncSession], task_id: int, data: dict[str, object]
) -> None:
    """Best-effort: mirrors a `task.progress` event onto the `Task` row in its
    own short session, since the stage is still mid-flight inside the main
    session held by `run_stage`."""
    with contextlib.suppress(Exception):
        async with session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            progress = data.get("progress")
            if isinstance(progress, int | float):
                task.progress = float(progress)
            if "eta_seconds" in data:
                eta = data["eta_seconds"]
                task.eta_seconds = int(eta) if isinstance(eta, int | float) else None
            message = data.get("message")
            if isinstance(message, str):
                task.message = message
            await session.commit()
