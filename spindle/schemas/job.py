"""DTOs for `/api/v1/jobs`, per architecture.md §4 and phase-07 task 1.

Never reuse a table model directly (CLAUDE.md rule 2 is TS-specific, but the
same discipline applies backend-side too): `JobSummary`/`JobDetail` are built
from a `Job` row plus its `Task` rows, since progress/current_task/eta are
derived, not stored columns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from spindle.models.disc import Disc, DiscType
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.media_file import MediaFile
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.pipeline.stages import current_task_eta_seconds, current_task_kind, job_progress


class TaskOut(BaseModel):
    kind: TaskKind
    status: TaskStatus
    progress: float
    eta_seconds: int | None
    message: str | None
    error: str | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_model(cls, task: Task) -> TaskOut:
        return cls(
            kind=task.kind,
            status=task.status,
            progress=task.progress,
            eta_seconds=task.eta_seconds,
            message=task.message,
            error=task.error,
            attempts=task.attempts,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )


class MediaFileOut(BaseModel):
    id: int
    path: str
    role: str
    size_bytes: int | None
    duration_seconds: int | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    language: str | None
    is_forced: bool

    @classmethod
    def from_model(cls, media_file: MediaFile) -> MediaFileOut:
        return cls(
            id=media_file.id,  # type: ignore[arg-type]
            path=media_file.path,
            role=media_file.role,
            size_bytes=media_file.size_bytes,
            duration_seconds=media_file.duration_seconds,
            video_codec=media_file.video_codec,
            audio_codec=media_file.audio_codec,
            width=media_file.width,
            height=media_file.height,
            language=media_file.language,
            is_forced=media_file.is_forced,
        )


class DiscOut(BaseModel):
    id: int
    disc_type: DiscType
    label: str | None
    size_bytes: int | None
    detected_at: datetime

    @classmethod
    def from_model(cls, disc: Disc) -> DiscOut:
        return cls(
            id=disc.id,  # type: ignore[arg-type]
            disc_type=disc.disc_type,
            label=disc.label,
            size_bytes=disc.size_bytes,
            detected_at=disc.detected_at,
        )


class MetadataCandidate(BaseModel):
    tmdb_id: int | None = None
    title: str = ""
    year: int | None = None
    poster_url: str | None = None
    confidence: float = 0.0


class JobSummary(BaseModel):
    id: int
    status: JobStatus
    media_kind: MediaKind
    title: str | None
    year: int | None
    poster_url: str | None
    current_task: TaskKind | None
    progress: float
    eta_seconds: int | None
    drive_id: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, job: Job, tasks: list[Task], *, drive_id: int | None) -> JobSummary:
        return cls(
            id=job.id,  # type: ignore[arg-type]
            status=job.status,
            media_kind=job.media_kind,
            title=job.title,
            year=job.year,
            poster_url=job.poster_url,
            current_task=current_task_kind(tasks),
            progress=job_progress(tasks),
            eta_seconds=current_task_eta_seconds(tasks),
            drive_id=drive_id,
            created_at=job.created_at,
        )


class JobDetail(JobSummary):
    tasks: list[TaskOut]
    media_files: list[MediaFileOut]
    disc: DiscOut | None
    confidence: float | None
    candidates: list[MetadataCandidate]
    error: str | None
    meta: dict[str, Any]

    @classmethod
    def from_models(
        cls,
        job: Job,
        tasks: list[Task],
        *,
        disc: Disc | None,
        media_files: list[MediaFile],
        drive_id: int | None,
    ) -> JobDetail:
        summary = JobSummary.from_model(job, tasks, drive_id=drive_id)
        raw_candidates = job.meta.get("candidates") if isinstance(job.meta, dict) else None
        candidates = (
            [MetadataCandidate.model_validate(c) for c in raw_candidates]
            if isinstance(raw_candidates, list)
            else []
        )
        return cls(
            **summary.model_dump(),
            tasks=[TaskOut.from_model(t) for t in tasks],
            media_files=[MediaFileOut.from_model(m) for m in media_files],
            disc=DiscOut.from_model(disc) if disc is not None else None,
            confidence=job.confidence,
            candidates=candidates,
            error=job.error,
            meta=job.meta,
        )


class JobPatch(BaseModel):
    """`PATCH /jobs/{id}` — allowed only while `queued`, `paused` or
    `needs_review` (phase-07 task 2). `episodes` has no dedicated `Job`
    column; it's stashed in `Job.meta` alongside the other naming-template
    extras (`edition`, `episode_title`) that `OrganizeStage` already reads."""

    title: str | None = None
    year: int | None = None
    media_kind: MediaKind | None = None
    tmdb_id: int | None = None
    season: int | None = None
    episodes: list[int] | None = None


class JobCreate(BaseModel):
    """`POST /jobs` — a manual job from an existing file (no disc)."""

    source_path: str
    media_kind: MediaKind = MediaKind.MOVIE
    title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None


class JobRetryRequest(BaseModel):
    from_stage: TaskKind | None = None
