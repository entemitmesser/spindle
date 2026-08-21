from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from spindle.models.disc import Disc, DiscType
from spindle.models.drive import Drive, DriveStatus
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.task import Task, TaskKind, TaskStatus


async def make_drive(session: AsyncSession, **overrides: Any) -> Drive:
    defaults: dict[str, Any] = {"device_path": "/dev/sr0", "status": DriveStatus.EMPTY}
    drive = Drive(**{**defaults, **overrides})
    session.add(drive)
    await session.commit()
    await session.refresh(drive)
    return drive


async def make_disc(session: AsyncSession, **overrides: Any) -> Disc:
    if "drive_id" not in overrides:
        overrides["drive_id"] = (await make_drive(session)).id
    defaults: dict[str, Any] = {"disc_type": DiscType.DVD}
    disc = Disc(**{**defaults, **overrides})
    session.add(disc)
    await session.commit()
    await session.refresh(disc)
    return disc


async def make_job(session: AsyncSession, **overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "status": JobStatus.QUEUED,
        "media_kind": MediaKind.MOVIE,
        "source_path": "/mnt/media/movie.mkv",
    }
    job = Job(**{**defaults, **overrides})
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def make_task(session: AsyncSession, **overrides: Any) -> Task:
    if "job_id" not in overrides:
        overrides["job_id"] = (await make_job(session)).id
    defaults: dict[str, Any] = {"kind": TaskKind.IDENTIFY, "status": TaskStatus.PENDING}
    task = Task(**{**defaults, **overrides})
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def make_job_with_tasks(session: AsyncSession, **overrides: Any) -> tuple[Job, list[Task]]:
    """A `Job` plus one `Task` per `TaskKind`, in pipeline order — the shape
    every real job has (`DriveWatcher`/`POST /jobs` both create tasks this
    way), for routes/tests that read `JobSummary.progress`/`current_task`."""
    job = await make_job(session, **overrides)
    tasks = []
    for kind in TaskKind:
        task = Task(job_id=job.id, kind=kind, status=TaskStatus.PENDING)
        session.add(task)
        tasks.append(task)
    await session.commit()
    for task in tasks:
        await session.refresh(task)
    return job, tasks
