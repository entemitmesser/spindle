from __future__ import annotations

from pydantic import BaseModel

from spindle.schemas.job import JobSummary


class QueueResponse(BaseModel):
    items: list[JobSummary]


class QueueReorderRequest(BaseModel):
    job_ids: list[int]
