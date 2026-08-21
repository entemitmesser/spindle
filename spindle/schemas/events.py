from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from spindle.models.event import Event


class EventOut(BaseModel):
    id: int
    type: str
    ts: datetime
    job_id: int | None
    drive_id: int | None
    data: dict[str, Any]

    @classmethod
    def from_model(cls, event: Event) -> EventOut:
        return cls(
            id=event.id,  # type: ignore[arg-type]
            type=event.type,
            ts=event.ts,
            job_id=event.job_id,
            drive_id=event.drive_id,
            data=event.data,
        )
