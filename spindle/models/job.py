from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlmodel import CheckConstraint, Column, Field, Index, SQLModel

from spindle.models.types import MutableJSON, UTCDateTime, utc_now


class MediaKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    MUSIC = "music"
    OTHER = "other"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(SQLModel, table=True):
    __table_args__ = (
        Index("ix_job_status_created_at", "status", "created_at"),
        CheckConstraint(
            "disc_id IS NOT NULL OR source_path IS NOT NULL", name="ck_job_disc_or_source"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    disc_id: int | None = Field(default=None, foreign_key="disc.id")
    source_path: str | None = None
    status: JobStatus = Field(default=JobStatus.QUEUED)
    priority: int = Field(default=0)
    media_kind: MediaKind = Field(default=MediaKind.OTHER)
    title: str | None = None
    original_title: str | None = None
    year: int | None = None
    season: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    confidence: float | None = None
    poster_url: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableJSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    finished_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    updated_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
