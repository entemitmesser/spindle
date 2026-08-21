from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from spindle.models.disc import Disc
from spindle.models.drive import Drive
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.media_file import MediaFile
from spindle.models.task import Task, TaskStatus

_RUNNABLE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
_RUNNABLE_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.FAILED)


async def get_drive_by_path(session: AsyncSession, device_path: str) -> Drive | None:
    result = await session.execute(select(Drive).where(col(Drive.device_path) == device_path))
    return result.scalar_one_or_none()


async def get_latest_job_by_fingerprint(
    session: AsyncSession, fingerprint: str, *, exclude_disc_id: int | None = None
) -> Job | None:
    """The most recent job for a disc with this fingerprint, for re-insert reuse."""
    stmt = (
        select(Job)
        .join(Disc, col(Job.disc_id) == col(Disc.id))
        .where(col(Disc.fingerprint) == fingerprint)
        .order_by(col(Job.created_at).desc())
    )
    if exclude_disc_id is not None:
        stmt = stmt.where(col(Disc.id) != exclude_disc_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


async def get_active_jobs(session: AsyncSession) -> list[Job]:
    stmt = (
        select(Job)
        .where(col(Job.status).in_(_RUNNABLE_JOB_STATUSES))
        .order_by(col(Job.priority).desc(), col(Job.created_at).asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_runnable_tasks(session: AsyncSession, job_id: int) -> list[Task]:
    stmt = (
        select(Task)
        .where(col(Task.job_id) == job_id, col(Task.status).in_(_RUNNABLE_TASK_STATUSES))
        .order_by(col(Task.id).asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_tasks_for_job(session: AsyncSession, job_id: int) -> list[Task]:
    """All of a job's tasks in `TaskKind` pipeline order (task ids are
    assigned in that order at job creation) — unlike `get_runnable_tasks`,
    this includes terminal/running tasks too, so the dispatcher can detect a
    blocked pipeline instead of skipping past a stuck stage."""
    stmt = select(Task).where(col(Task.job_id) == job_id).order_by(col(Task.id).asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_job(session: AsyncSession, job_id: int) -> Job | None:
    return await session.get(Job, job_id)


async def get_drive_id_for_job(session: AsyncSession, job: Job) -> int | None:
    if job.disc_id is None:
        return None
    disc = await session.get(Disc, job.disc_id)
    return disc.drive_id if disc is not None else None


async def get_media_files_for_job(session: AsyncSession, job_id: int) -> list[MediaFile]:
    stmt = select(MediaFile).where(col(MediaFile.job_id) == job_id).order_by(col(MediaFile.id))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_jobs(
    session: AsyncSession,
    *,
    statuses: list[JobStatus] | None = None,
    media_kind: MediaKind | None = None,
    query: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    stmt = select(Job)
    if statuses:
        stmt = stmt.where(col(Job.status).in_(statuses))
    if media_kind is not None:
        stmt = stmt.where(col(Job.media_kind) == media_kind)
    if query:
        stmt = stmt.where(col(Job.title).ilike(f"%{query}%"))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    stmt = stmt.order_by(col(Job.created_at).desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def get_drives(session: AsyncSession) -> list[Drive]:
    result = await session.execute(select(Drive).order_by(col(Drive.device_path)))
    return list(result.scalars().all())


async def get_drive(session: AsyncSession, drive_id: int) -> Drive | None:
    return await session.get(Drive, drive_id)


async def get_latest_disc_for_drive(session: AsyncSession, drive_id: int) -> Disc | None:
    stmt = (
        select(Disc)
        .where(col(Disc.drive_id) == drive_id)
        .order_by(col(Disc.detected_at).desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
