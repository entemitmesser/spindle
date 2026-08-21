"""`/api/v1/jobs`, per architecture.md §4 and phase-07-api-realtime.md task 2."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.config import Settings
from spindle.errors import ConflictError, NotFoundError, ValidationFailedError
from spindle.models.disc import Disc
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.schemas.common import DEFAULT_LIMIT, MAX_LIMIT, Page
from spindle.schemas.job import JobCreate, JobDetail, JobPatch, JobRetryRequest, JobSummary
from spindle.services import repo
from spindle.services.events import EventBus, EventIn
from spindle.services.queue import Dispatcher
from spindle.utils import fsutil

router = APIRouter()

_MUTABLE_STATUSES = (JobStatus.QUEUED, JobStatus.PAUSED, JobStatus.NEEDS_REVIEW)
_MAX_LOG_BYTES = 5 * 1024 * 1024


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


def _event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus  # type: ignore[no-any-return]


def _dispatcher(request: Request) -> Dispatcher | None:
    return getattr(request.app.state, "dispatcher", None)


def _wake_dispatcher(request: Request) -> None:
    dispatcher = _dispatcher(request)
    if dispatcher is not None:
        dispatcher.wake_event.set()


async def _summary(session: AsyncSession, job: Job) -> JobSummary:
    tasks = await repo.get_tasks_for_job(session, job.id)  # type: ignore[arg-type]
    drive_id = await repo.get_drive_id_for_job(session, job)
    return JobSummary.from_model(job, tasks, drive_id=drive_id)


async def _detail(session: AsyncSession, job: Job) -> JobDetail:
    tasks = await repo.get_tasks_for_job(session, job.id)  # type: ignore[arg-type]
    drive_id = await repo.get_drive_id_for_job(session, job)
    disc = await session.get(Disc, job.disc_id) if job.disc_id is not None else None
    media_files = await repo.get_media_files_for_job(session, job.id)  # type: ignore[arg-type]
    return JobDetail.from_models(job, tasks, disc=disc, media_files=media_files, drive_id=drive_id)


def _tail_bytes(path: Path, max_bytes: int) -> bytes:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read()


def _apply_tail_lines(data: bytes, tail: int | None) -> bytes:
    if tail is None:
        return data
    lines = data.splitlines(keepends=True)
    return b"".join(lines[-tail:]) if tail > 0 else b""


def _log_response(data: bytes, range_header: str | None) -> Response:
    total = len(data)
    if range_header and range_header.startswith("bytes="):
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = min(int(end_s), total - 1) if end_s else total - 1
        except ValueError:
            start, end = 0, total - 1
        if total > 0 and 0 <= start <= end < total:
            return Response(
                content=data[start : end + 1],
                status_code=206,
                media_type="text/plain",
                headers={"Content-Range": f"bytes {start}-{end}/{total}", "Accept-Ranges": "bytes"},
            )
    return Response(content=data, media_type="text/plain", headers={"Accept-Ranges": "bytes"})


@router.get("", operation_id="list_jobs")
async def list_jobs(
    request: Request,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    media_kind: MediaKind | None = None,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Page[JobSummary]:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    async with _session_factory(request)() as session:
        jobs, total = await repo.list_jobs(
            session, statuses=status, media_kind=media_kind, query=q, limit=limit, offset=offset
        )
        items = [await _summary(session, job) for job in jobs]
    return Page[JobSummary](items=items, total=total, limit=limit, offset=offset)


@router.post("", operation_id="create_job", status_code=201)
async def create_job(request: Request, body: JobCreate) -> JobDetail:
    if not await asyncio.to_thread(Path(body.source_path).is_file):
        raise ValidationFailedError(f"No file at {body.source_path}.")

    async with _session_factory(request)() as session:
        job = Job(
            source_path=body.source_path,
            status=JobStatus.QUEUED,
            media_kind=body.media_kind,
            title=body.title,
            year=body.year,
            tmdb_id=body.tmdb_id,
        )
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
        detail = await _detail(session, job)

    await _event_bus(request).emit(
        EventIn(
            type="job.created",
            job_id=job.id,
            data={"title": job.title, "media_kind": job.media_kind.value},
        )
    )
    _wake_dispatcher(request)
    return detail


@router.get("/{job_id}", operation_id="get_job")
async def get_job(request: Request, job_id: int) -> JobDetail:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        return await _detail(session, job)


@router.patch("/{job_id}", operation_id="update_job")
async def update_job(request: Request, job_id: int, body: JobPatch) -> JobDetail:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        if job.status not in _MUTABLE_STATUSES:
            raise ConflictError(f"Job {job_id} cannot be edited while {job.status.value}.")

        if body.title is not None:
            job.title = body.title
        if body.year is not None:
            job.year = body.year
        if body.media_kind is not None:
            job.media_kind = body.media_kind
        if body.tmdb_id is not None:
            job.tmdb_id = body.tmdb_id
        if body.season is not None:
            job.season = body.season
        if body.episodes is not None:
            job.meta = {**job.meta, "episodes": body.episodes}
        job.updated_at = utc_now()
        await session.commit()
        await session.refresh(job)
        detail = await _detail(session, job)

    await _event_bus(request).emit(
        EventIn(type="job.updated", job_id=job_id, data=body.model_dump(exclude_unset=True))
    )
    return detail


@router.post("/{job_id}/retry", operation_id="retry_job")
async def retry_job(
    request: Request, job_id: int, body: JobRetryRequest | None = None
) -> JobDetail:
    order = list(TaskKind)
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        if job.status == JobStatus.RUNNING:
            raise ConflictError(f"Job {job_id} is already running.")

        tasks = await repo.get_tasks_for_job(session, job_id)
        from_stage = body.from_stage if body is not None else None
        if from_stage is None:
            non_terminal = [
                t for t in tasks if t.status not in (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED)
            ]
            from_stage = non_terminal[0].kind if non_terminal else order[0]
        start_index = order.index(from_stage)

        for task in tasks:
            if order.index(task.kind) < start_index:
                continue
            task.status = TaskStatus.PENDING
            task.progress = 0.0
            task.eta_seconds = None
            task.message = None
            task.error = None
            task.attempts = 0
            task.started_at = None
            task.finished_at = None

        old_status = job.status
        job.status = JobStatus.QUEUED
        job.error = None
        job.finished_at = None
        job.updated_at = utc_now()
        await session.commit()
        await session.refresh(job)
        detail = await _detail(session, job)

    await _event_bus(request).emit(
        EventIn(
            type="job.status_changed",
            job_id=job_id,
            data={"from": old_status.value, "to": job.status.value, "reason": "user_retry"},
        )
    )
    _wake_dispatcher(request)
    return detail


@router.post("/{job_id}/cancel", operation_id="cancel_job")
async def cancel_job(request: Request, job_id: int) -> JobDetail:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            raise ConflictError(f"Job {job_id} is already {job.status.value}.")

        tasks = await repo.get_tasks_for_job(session, job_id)
        running_task = next((t for t in tasks if t.status == TaskStatus.RUNNING), None)

        old_status = job.status
        job.status = JobStatus.CANCELLED
        job.finished_at = utc_now()
        job.updated_at = utc_now()
        await session.commit()
        await session.refresh(job)
        detail = await _detail(session, job)

    dispatcher = _dispatcher(request)
    if dispatcher is not None and running_task is not None and running_task.id is not None:
        dispatcher.cancel_task(running_task.id)

    await _event_bus(request).emit(
        EventIn(
            type="job.status_changed",
            job_id=job_id,
            data={"from": old_status.value, "to": job.status.value, "reason": "user_cancel"},
        )
    )
    return detail


@router.post("/{job_id}/pause", operation_id="pause_job")
async def pause_job(request: Request, job_id: int) -> JobDetail:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise ConflictError(f"Job {job_id} cannot be paused while {job.status.value}.")

        old_status = job.status
        job.status = JobStatus.PAUSED
        job.updated_at = utc_now()
        await session.commit()
        await session.refresh(job)
        detail = await _detail(session, job)

    await _event_bus(request).emit(
        EventIn(
            type="job.status_changed",
            job_id=job_id,
            data={"from": old_status.value, "to": job.status.value, "reason": "user_pause"},
        )
    )
    return detail


@router.post("/{job_id}/resume", operation_id="resume_job")
async def resume_job(request: Request, job_id: int) -> JobDetail:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        if job.status != JobStatus.PAUSED:
            raise ConflictError(f"Job {job_id} is not paused.")

        old_status = job.status
        job.status = JobStatus.QUEUED
        job.updated_at = utc_now()
        await session.commit()
        await session.refresh(job)
        detail = await _detail(session, job)

    await _event_bus(request).emit(
        EventIn(
            type="job.status_changed",
            job_id=job_id,
            data={"from": old_status.value, "to": job.status.value, "reason": "user_resume"},
        )
    )
    _wake_dispatcher(request)
    return detail


@router.delete("/{job_id}", operation_id="delete_job", status_code=204)
async def delete_job(request: Request, job_id: int, purge_files: bool = False) -> Response:
    settings: Settings = request.app.state.settings
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        tasks = await repo.get_tasks_for_job(session, job_id)
        if any(t.status == TaskStatus.RUNNING for t in tasks):
            raise ConflictError(f"Job {job_id} has a task running; cancel it first.")

        destination = job.meta.get("organize", {}).get("destination")
        for task in tasks:
            await session.delete(task)
        await session.commit()
        await session.delete(job)
        await session.commit()

    if purge_files:
        await asyncio.to_thread(_purge_job_files, settings, job_id, destination)

    return Response(status_code=204)


def _purge_job_files(settings: Settings, job_id: int, destination: str | None) -> None:
    for subdir in ("raw", "transcode"):
        shutil.rmtree(settings.work_dir / subdir / fsutil.job_dir_name(job_id), ignore_errors=True)
    if not destination:
        return
    dest_path = Path(destination)
    try:
        inside_library = dest_path.resolve().is_relative_to(settings.library_dir.resolve())
    except OSError:
        inside_library = False
    if not inside_library:
        return
    if dest_path.is_dir():
        shutil.rmtree(dest_path, ignore_errors=True)
    else:
        dest_path.unlink(missing_ok=True)


def _read_log(log_path: Path, tail: int | None) -> bytes | None:
    if not log_path.is_file():
        return None
    return _apply_tail_lines(_tail_bytes(log_path, _MAX_LOG_BYTES), tail)


@router.get("/{job_id}/logs/{kind}", operation_id="get_job_log")
async def get_job_log(
    request: Request, job_id: int, kind: TaskKind, tail: int | None = None
) -> Response:
    async with _session_factory(request)() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise NotFoundError(f"Job {job_id} not found.")
        tasks = await repo.get_tasks_for_job(session, job_id)
        task = next((t for t in tasks if t.kind == kind), None)

    if task is None or task.log_path is None:
        raise NotFoundError(f"No log for {kind.value} on job {job_id}.")

    data = await asyncio.to_thread(_read_log, Path(task.log_path), tail)
    if data is None:
        raise NotFoundError(f"No log for {kind.value} on job {job_id}.")
    return _log_response(data, request.headers.get("range"))
