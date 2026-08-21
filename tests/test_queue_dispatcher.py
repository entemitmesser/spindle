from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.config import BehaviourConfig, ConfigStore, RuntimeConfig
from spindle.models.event import Event
from spindle.models.job import Job, JobStatus
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import StageResult
from spindle.services import queue as queue_module
from spindle.services.events import EventBus
from spindle.services.queue import (
    Dispatcher,
    _all_terminal_success,
    _is_retry_eligible,
    _select_next_task,
    run_stage,
)
from tests.factories import make_disc, make_drive, make_job, make_task


class _FakeStage:
    def __init__(
        self,
        kind: TaskKind,
        *,
        gate: asyncio.Event | None = None,
        fail: bool = False,
        skip: bool = False,
    ) -> None:
        self.kind = kind
        self.gate = gate
        self.fail = fail
        self.skip = skip
        self.run_calls: list[int] = []
        self.cleanup_calls: list[int] = []

    async def can_skip(self, ctx: JobContext) -> bool:
        return self.skip

    async def run(self, ctx: JobContext) -> StageResult:
        assert ctx.job.id is not None
        self.run_calls.append(ctx.job.id)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            return StageResult(status=TaskStatus.FAILED, message="boom")
        return StageResult(status=TaskStatus.SUCCEEDED)

    async def cleanup(self, ctx: JobContext) -> None:
        assert ctx.job.id is not None
        self.cleanup_calls.append(ctx.job.id)


_next_device_index = 0


async def _seed_job(
    session: AsyncSession, *, kinds: list[TaskKind], with_drive: bool = False
) -> tuple[Job, dict[TaskKind, Task]]:
    global _next_device_index
    disc_id = None
    if with_drive:
        _next_device_index += 1
        drive = await make_drive(session, device_path=f"/dev/sr{_next_device_index}")
        disc = await make_disc(session, drive_id=drive.id)
        disc_id = disc.id
    job = await make_job(
        session,
        disc_id=disc_id,
        source_path=None if disc_id is not None else "/mnt/media/job.mkv",
    )
    tasks = {
        kind: await make_task(session, job_id=job.id, kind=kind, status=TaskStatus.PENDING)
        for kind in kinds
    }
    return job, tasks


async def _wait_until(
    predicate: object, *, deadline_seconds: float = 2.0, interval: float = 0.01
) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_seconds
    while loop.time() < deadline:
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(interval)
    pytest.fail("condition was never met before the timeout")


def _dispatcher(
    stages: dict[TaskKind, _FakeStage],
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> Dispatcher:
    return Dispatcher(
        stages,  # type: ignore[arg-type]
        session_factory,
        event_bus,
        config_store,
        work_dir=tmp_path,
        data_dir=tmp_path,
    )


# -- pure scheduling policy (no DB/event loop needed) -----------------------


def _task(
    kind: TaskKind,
    status: TaskStatus,
    *,
    attempts: int = 0,
    finished_at: object = None,
    task_id: int = 1,
) -> Task:
    return Task(
        id=task_id, job_id=1, kind=kind, status=status, attempts=attempts, finished_at=finished_at
    )


def test_is_retry_eligible_false_for_rip_and_transcode() -> None:
    config = RuntimeConfig()
    now = utc_now()
    for kind in (TaskKind.RIP, TaskKind.TRANSCODE):
        task = _task(kind, TaskStatus.FAILED, finished_at=now)
        assert _is_retry_eligible(task, config, now) is False


def test_is_retry_eligible_false_once_max_retries_exhausted() -> None:
    config = RuntimeConfig(behaviour=BehaviourConfig(max_retries=2))
    now = utc_now()
    task = _task(TaskKind.IDENTIFY, TaskStatus.FAILED, attempts=2, finished_at=now)
    assert _is_retry_eligible(task, config, now) is False


def test_is_retry_eligible_false_before_backoff_elapses() -> None:
    config = RuntimeConfig(behaviour=BehaviourConfig(max_retries=2))
    now = utc_now()
    task = _task(TaskKind.SUBTITLES, TaskStatus.FAILED, attempts=0, finished_at=now)
    assert _is_retry_eligible(task, config, now) is False


def test_is_retry_eligible_true_once_backoff_has_elapsed() -> None:
    config = RuntimeConfig(behaviour=BehaviourConfig(max_retries=2))
    now = utc_now()
    task = _task(
        TaskKind.SUBTITLES, TaskStatus.FAILED, attempts=0, finished_at=now - timedelta(seconds=31)
    )
    assert _is_retry_eligible(task, config, now) is True


def test_select_next_task_returns_first_pending_task_in_pipeline_order() -> None:
    config = RuntimeConfig()
    now = utc_now()
    tasks = [
        _task(TaskKind.IDENTIFY, TaskStatus.SUCCEEDED, task_id=1),
        _task(TaskKind.RIP, TaskStatus.PENDING, task_id=2),
        _task(TaskKind.TRANSCODE, TaskStatus.PENDING, task_id=3),
    ]
    selected = _select_next_task(tasks, config, now)
    assert selected is not None
    assert selected.id == 2


def test_select_next_task_blocks_on_a_still_running_task() -> None:
    config = RuntimeConfig()
    now = utc_now()
    tasks = [
        _task(TaskKind.RIP, TaskStatus.RUNNING, task_id=1),
        _task(TaskKind.TRANSCODE, TaskStatus.PENDING, task_id=2),
    ]
    assert _select_next_task(tasks, config, now) is None


def test_select_next_task_never_skips_a_non_retryable_failed_task() -> None:
    config = RuntimeConfig()
    now = utc_now()
    tasks = [
        _task(TaskKind.RIP, TaskStatus.FAILED, task_id=1, finished_at=now),
        _task(TaskKind.TRANSCODE, TaskStatus.PENDING, task_id=2),
    ]
    assert _select_next_task(tasks, config, now) is None


def test_select_next_task_returns_a_retry_eligible_failed_task() -> None:
    config = RuntimeConfig(behaviour=BehaviourConfig(max_retries=2))
    now = utc_now()
    tasks = [
        _task(
            TaskKind.IDENTIFY,
            TaskStatus.FAILED,
            task_id=1,
            attempts=0,
            finished_at=now - timedelta(minutes=5),
        )
    ]
    selected = _select_next_task(tasks, config, now)
    assert selected is not None
    assert selected.id == 1


def test_all_terminal_success() -> None:
    tasks = [
        _task(TaskKind.IDENTIFY, TaskStatus.SUCCEEDED, task_id=1),
        _task(TaskKind.RIP, TaskStatus.SKIPPED, task_id=2),
    ]
    assert _all_terminal_success(tasks) is True
    pending = [*tasks, _task(TaskKind.TRANSCODE, TaskStatus.PENDING, task_id=3)]
    assert _all_terminal_success(pending) is False
    assert _all_terminal_success([]) is False


# -- run_stage ---------------------------------------------------------------


async def test_run_stage_marks_a_skippable_task_skipped_without_calling_run(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    stage = _FakeStage(TaskKind.NOTIFY, skip=True)
    job, tasks = await _seed_job(session, kinds=[TaskKind.NOTIFY])

    await run_stage(
        stage=stage,  # type: ignore[arg-type]
        session_factory=session_factory,
        event_bus=event_bus,
        config_store=config_store,
        work_dir=tmp_path,
        data_dir=tmp_path,
        job_id=job.id,
        task_id=tasks[TaskKind.NOTIFY].id,
    )

    assert stage.run_calls == []
    async with session_factory() as s:
        refreshed = await s.get(Task, tasks[TaskKind.NOTIFY].id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.SKIPPED
    assert refreshed.progress == 1.0


# -- Dispatcher ----------------------------------------------------------


async def test_two_drives_rip_in_parallel_while_one_transcode_runs_and_a_third_waits(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    rip_gate = asyncio.Event()
    transcode_gate = asyncio.Event()
    rip_stage = _FakeStage(TaskKind.RIP, gate=rip_gate)
    transcode_stage = _FakeStage(TaskKind.TRANSCODE, gate=transcode_gate)
    dispatcher = _dispatcher(
        {TaskKind.RIP: rip_stage, TaskKind.TRANSCODE: transcode_stage},
        session_factory,
        event_bus,
        config_store,
        tmp_path,
    )
    await config_store.update({"transcode": {"workers": 1}})

    job1, _tasks1 = await _seed_job(session, kinds=[TaskKind.RIP], with_drive=True)
    job2, _tasks2 = await _seed_job(session, kinds=[TaskKind.RIP], with_drive=True)
    job3, _tasks3 = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    job4, _tasks4 = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await dispatcher.poll_once()
    await _wait_until(lambda: len(rip_stage.run_calls) == 2 and len(transcode_stage.run_calls) == 1)

    assert dispatcher.running_count(TaskKind.RIP) == 2
    assert dispatcher.running_count(TaskKind.TRANSCODE) == 1
    assert sorted(rip_stage.run_calls) == sorted([job1.id, job2.id])
    assert transcode_stage.run_calls == [job3.id]
    assert job4.id not in transcode_stage.run_calls

    transcode_gate.set()
    await _wait_until(lambda: dispatcher.running_count(TaskKind.TRANSCODE) == 0)

    await dispatcher.poll_once()
    await _wait_until(lambda: job4.id in transcode_stage.run_calls)

    rip_gate.set()
    await _wait_until(lambda: dispatcher.running_count(TaskKind.RIP) == 0)


async def test_transcode_workers_two_allows_two_concurrent_transcodes(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    stage = _FakeStage(TaskKind.TRANSCODE, gate=gate)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )
    await config_store.update({"transcode": {"workers": 2}})

    job1, _ = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    job2, _ = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await dispatcher.poll_once()
    await _wait_until(lambda: len(stage.run_calls) == 2)

    assert dispatcher.running_count(TaskKind.TRANSCODE) == 2
    assert sorted(stage.run_calls) == sorted([job1.id, job2.id])

    gate.set()
    await _wait_until(lambda: dispatcher.running_count(TaskKind.TRANSCODE) == 0)


async def test_changing_transcode_workers_takes_effect_without_a_restart(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    stage = _FakeStage(TaskKind.TRANSCODE, gate=gate)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )
    await config_store.update({"transcode": {"workers": 1}})

    job1, _ = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    job2, _ = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await dispatcher.poll_once()
    await _wait_until(lambda: len(stage.run_calls) == 1)
    assert dispatcher.running_count(TaskKind.TRANSCODE) == 1

    # No new Dispatcher, no restart -- just a settings update and another poll.
    await config_store.update({"transcode": {"workers": 2}})
    await dispatcher.poll_once()
    await _wait_until(lambda: len(stage.run_calls) == 2)

    gate.set()
    await _wait_until(lambda: dispatcher.running_count(TaskKind.TRANSCODE) == 0)
    assert sorted(stage.run_calls) == sorted([job1.id, job2.id])


async def test_cancel_task_frees_the_capacity_slot(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    stage = _FakeStage(TaskKind.TRANSCODE, gate=gate)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )
    await config_store.update({"transcode": {"workers": 1}})

    job1, tasks1 = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    await dispatcher.poll_once()
    # Wait for the stage to actually be inside `run()` (blocked on the gate),
    # not just for the synchronously-updated capacity bookkeeping -- cancelling
    # before the task is truly mid-flight would just leave it `pending` again.
    await _wait_until(lambda: job1.id in stage.run_calls)
    assert dispatcher.running_count(TaskKind.TRANSCODE) == 1

    assert dispatcher.cancel_task(tasks1[TaskKind.TRANSCODE].id) is True
    await _wait_until(lambda: dispatcher.running_count(TaskKind.TRANSCODE) == 0)

    job2, _tasks2 = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    await dispatcher.poll_once()
    await _wait_until(lambda: job2.id in stage.run_calls)
    assert dispatcher.running_count(TaskKind.TRANSCODE) == 1


async def test_cancel_task_is_a_noop_for_an_unknown_task_id(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    dispatcher = _dispatcher({}, session_factory, event_bus, config_store, tmp_path)

    assert dispatcher.cancel_task(999) is False


async def test_recover_orphaned_tasks_resets_running_tasks_and_warns(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    job, tasks = await _seed_job(session, kinds=[TaskKind.TRANSCODE])
    task = tasks[TaskKind.TRANSCODE]
    task.status = TaskStatus.RUNNING
    task.started_at = utc_now()
    await session.commit()

    stage = _FakeStage(TaskKind.TRANSCODE)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )

    await dispatcher.recover_orphaned_tasks()

    async with session_factory() as s:
        refreshed = await s.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.started_at is None

    result = await session.execute(select(Event).where(col(Event.type) == "system.warning"))
    warnings = result.scalars().all()
    assert any(e.data.get("code") == "recovered_after_restart" for e in warnings)

    # and the recovered task is runnable again on the very next poll
    await dispatcher.poll_once()
    await _wait_until(lambda: job.id in stage.run_calls)


async def test_dispatcher_runs_a_jobs_stages_in_order_and_completes_it(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    identify_stage = _FakeStage(TaskKind.IDENTIFY)
    rip_stage = _FakeStage(TaskKind.RIP)
    dispatcher = _dispatcher(
        {TaskKind.IDENTIFY: identify_stage, TaskKind.RIP: rip_stage},
        session_factory,
        event_bus,
        config_store,
        tmp_path,
    )
    job, _tasks = await _seed_job(session, kinds=[TaskKind.IDENTIFY, TaskKind.RIP], with_drive=True)

    async def _job_completed() -> bool:
        async with session_factory() as s:
            refreshed = await s.get(Job, job.id)
            return refreshed is not None and refreshed.status == JobStatus.COMPLETED

    for _ in range(300):
        await dispatcher.poll_once()
        if await _job_completed():
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("job never reached completed")

    assert identify_stage.run_calls == [job.id]
    assert rip_stage.run_calls == [job.id]


async def test_a_failed_transcode_task_never_auto_retries_and_fails_the_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    stage = _FakeStage(TaskKind.TRANSCODE, fail=True)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )
    job, tasks = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await dispatcher.poll_once()
    await _wait_until(lambda: len(stage.run_calls) == 1)
    await _wait_until(lambda: dispatcher.running_count(TaskKind.TRANSCODE) == 0)

    async with session_factory() as s:
        refreshed_job = await s.get(Job, job.id)
        refreshed_task = await s.get(Task, tasks[TaskKind.TRANSCODE].id)
    assert refreshed_job is not None
    assert refreshed_task is not None
    assert refreshed_job.status == JobStatus.FAILED
    assert refreshed_task.status == TaskStatus.FAILED
    assert refreshed_task.attempts == 1

    # the job dropped out of (queued, running) -- a second poll must not touch it again
    await dispatcher.poll_once()
    assert stage.run_calls == [job.id]


# -- watchdog (phase-09-hardening.md task 2) ---------------------------------


async def test_watchdog_fails_a_stalled_task_and_cleans_up(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 "minute" -> 0.2s of real time, so the test doesn't actually wait a minute.
    monkeypatch.setattr(queue_module, "_SECONDS_PER_MINUTE", 0.2)
    await config_store.update({"behaviour": {"stall_timeout_minutes": 1}})

    stage = _FakeStage(TaskKind.TRANSCODE, gate=asyncio.Event())  # gate never set -> never returns
    job, tasks = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await run_stage(
        stage=stage,  # type: ignore[arg-type]
        session_factory=session_factory,
        event_bus=event_bus,
        config_store=config_store,
        work_dir=tmp_path,
        data_dir=tmp_path,
        job_id=job.id,
        task_id=tasks[TaskKind.TRANSCODE].id,
    )

    async with session_factory() as s:
        refreshed_task = await s.get(Task, tasks[TaskKind.TRANSCODE].id)
        refreshed_job = await s.get(Job, job.id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.FAILED
    assert refreshed_task.error is not None
    assert "No progress" in refreshed_task.error
    assert refreshed_job is not None
    assert refreshed_job.status == JobStatus.FAILED
    assert stage.cleanup_calls == [job.id]


async def test_watchdog_never_fires_while_progress_keeps_arriving(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow-but-moving transcode (e.g. UHD) must never be a false positive
    -- the watchdog is keyed off progress events, not wall clock."""
    # A stall window shorter than the stage's *total* runtime, but longer than
    # the gap between any two consecutive progress events -- if the watchdog
    # were keyed off wall clock instead of progress, this would fail.
    monkeypatch.setattr(queue_module, "_SECONDS_PER_MINUTE", 0.3)
    await config_store.update({"behaviour": {"stall_timeout_minutes": 1}})

    class _SlowButProgressingStage:
        kind = TaskKind.TRANSCODE

        async def can_skip(self, ctx: JobContext) -> bool:
            return False

        async def run(self, ctx: JobContext) -> StageResult:
            for _ in range(5):
                await asyncio.sleep(0.12)
                await ctx.emit(
                    queue_module.EventIn(
                        type="task.progress", data={"kind": "transcode", "progress": 0.5}
                    )
                )
            return StageResult(status=TaskStatus.SUCCEEDED)

        async def cleanup(self, ctx: JobContext) -> None:
            return None

    job, tasks = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await run_stage(
        stage=_SlowButProgressingStage(),  # type: ignore[arg-type]
        session_factory=session_factory,
        event_bus=event_bus,
        config_store=config_store,
        work_dir=tmp_path,
        data_dir=tmp_path,
        job_id=job.id,
        task_id=tasks[TaskKind.TRANSCODE].id,
    )

    async with session_factory() as s:
        refreshed_task = await s.get(Task, tasks[TaskKind.TRANSCODE].id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.SUCCEEDED


async def test_dispatcher_stop_cleans_up_but_leaves_the_task_running_for_recovery(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    """A real shutdown (`Dispatcher.stop()`) is not a stall: `stage.cleanup`
    still runs (so the subprocess doesn't leak), but the task is left
    `running` in the DB rather than `failed` -- `recover_orphaned_tasks`
    resets it to `pending` on the next boot instead of burning a retry."""
    gate = asyncio.Event()
    stage = _FakeStage(TaskKind.TRANSCODE, gate=gate)
    dispatcher = _dispatcher(
        {TaskKind.TRANSCODE: stage}, session_factory, event_bus, config_store, tmp_path
    )
    job, tasks = await _seed_job(session, kinds=[TaskKind.TRANSCODE])

    await dispatcher.poll_once()
    await _wait_until(lambda: job.id in stage.run_calls)

    await dispatcher.stop(grace_period=2.0)

    assert stage.cleanup_calls == [job.id]
    async with session_factory() as s:
        refreshed_task = await s.get(Task, tasks[TaskKind.TRANSCODE].id)
    assert refreshed_task is not None
    assert refreshed_task.status == TaskStatus.RUNNING
