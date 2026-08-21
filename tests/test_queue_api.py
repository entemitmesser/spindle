from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.models.job import JobStatus
from tests.factories import make_job


async def test_get_queue_orders_by_priority_then_created_at(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    low = await make_job(session, title="Low", status=JobStatus.QUEUED, priority=0)
    high = await make_job(session, title="High", status=JobStatus.QUEUED, priority=5)
    await make_job(session, title="Done", status=JobStatus.COMPLETED)

    response = await client.get("/api/v1/queue")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids == [high.id, low.id]


async def test_reorder_queue_updates_priority(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    first = await make_job(session, title="First", status=JobStatus.QUEUED)
    second = await make_job(session, title="Second", status=JobStatus.QUEUED)

    response = await client.post("/api/v1/queue/reorder", json={"job_ids": [second.id, first.id]})

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids == [second.id, first.id]
