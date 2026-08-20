from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, Field, Index, SQLModel

from spindle.models.types import MutableJSON, UTCDateTime, utc_now


class Event(SQLModel, table=True):
    __table_args__ = (Index("ix_event_job_id_id", "job_id", "id"),)

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    job_id: int | None = Field(default=None, foreign_key="job.id")
    drive_id: int | None = Field(default=None, foreign_key="drive.id")
    data: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableJSON, nullable=False)
    )
    ts: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False, index=True)
    )
