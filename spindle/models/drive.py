from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Column, Field, SQLModel

from spindle.models.types import UTCDateTime, utc_now


class DriveStatus(StrEnum):
    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    BUSY = "busy"
    ERROR = "error"
    DISABLED = "disabled"


class Drive(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    device_path: str = Field(unique=True, index=True)
    display_name: str | None = None
    model: str | None = None
    vendor: str | None = None
    status: DriveStatus = Field(default=DriveStatus.EMPTY)
    is_enabled: bool = Field(default=True)
    # No FK: drive -> job -> disc -> drive would be a circular FK dependency.
    # This is a denormalised pointer to the job currently occupying the drive.
    current_job_id: int | None = Field(default=None, index=True)
    last_seen_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    created_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
