from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Column, Field, SQLModel, UniqueConstraint

from spindle.models.types import UTCDateTime


class TaskKind(StrEnum):
    """Also the pipeline stage order — the dispatcher iterates this enum."""

    IDENTIFY = "identify"
    RIP = "rip"
    TRANSCODE = "transcode"
    SUBTITLES = "subtitles"
    ORGANIZE = "organize"
    NOTIFY = "notify"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_task_job_kind"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    kind: TaskKind
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    progress: float = Field(default=0.0)
    eta_seconds: int | None = None
    message: str | None = None
    error: str | None = None
    attempts: int = Field(default=0)
    log_path: str | None = None
    started_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    finished_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
