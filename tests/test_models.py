from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.models.job import Job
from spindle.models.task import Task, TaskKind
from tests.factories import make_disc, make_drive, make_job, make_task


async def test_factories_create_linked_rows(session: AsyncSession) -> None:
    drive = await make_drive(session)
    disc = await make_disc(session, drive_id=drive.id)
    job = await make_job(session, disc_id=disc.id, source_path=None)
    task = await make_task(session, job_id=job.id, kind=TaskKind.RIP)

    assert disc.drive_id == drive.id
    assert job.disc_id == disc.id
    assert task.job_id == job.id


async def test_task_kind_is_unique_per_job(session: AsyncSession) -> None:
    job = await make_job(session)
    await make_task(session, job_id=job.id, kind=TaskKind.IDENTIFY)

    with pytest.raises(IntegrityError):
        session.add(Task(job_id=job.id, kind=TaskKind.IDENTIFY))
        await session.commit()


async def test_job_requires_disc_id_or_source_path(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        session.add(Job(disc_id=None, source_path=None))
        await session.commit()
