"""`/api/v1/queue`, per architecture.md §4 — the dispatcher's own runnable
view, reusing `services.repo.get_active_jobs`'s ordering (priority desc,
created_at asc) so this endpoint can never disagree with what actually
runs next."""

from __future__ import annotations

from fastapi import APIRouter, Request

from spindle.schemas.job import JobSummary
from spindle.schemas.queue import QueueReorderRequest, QueueResponse
from spindle.services import repo
from spindle.services.queue import Dispatcher

router = APIRouter()


@router.get("", operation_id="get_queue")
async def get_queue(request: Request) -> QueueResponse:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        jobs = await repo.get_active_jobs(session)
        items = []
        for job in jobs:
            tasks = await repo.get_tasks_for_job(session, job.id)  # type: ignore[arg-type]
            drive_id = await repo.get_drive_id_for_job(session, job)
            items.append(JobSummary.from_model(job, tasks, drive_id=drive_id))
    return QueueResponse(items=items)


@router.post("/reorder", operation_id="reorder_queue")
async def reorder_queue(request: Request, body: QueueReorderRequest) -> QueueResponse:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        jobs = await repo.get_active_jobs(session)
        by_id = {job.id: job for job in jobs}
        total = len(body.job_ids)
        for index, job_id in enumerate(body.job_ids):
            job = by_id.get(job_id)
            if job is not None:
                job.priority = total - index
        await session.commit()

        jobs = await repo.get_active_jobs(session)
        items = []
        for job in jobs:
            tasks = await repo.get_tasks_for_job(session, job.id)  # type: ignore[arg-type]
            drive_id = await repo.get_drive_id_for_job(session, job)
            items.append(JobSummary.from_model(job, tasks, drive_id=drive_id))

    dispatcher: Dispatcher | None = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake_event.set()
    return QueueResponse(items=items)
