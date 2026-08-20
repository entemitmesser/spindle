from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from spindle.models.drive import Drive
from spindle.models.job import Job, JobStatus
from spindle.models.task import Task, TaskStatus

_RUNNABLE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
_RUNNABLE_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.FAILED)


async def get_drive_by_path(session: AsyncSession, device_path: str) -> Drive | None:
    result = await session.execute(select(Drive).where(col(Drive.device_path) == device_path))
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
