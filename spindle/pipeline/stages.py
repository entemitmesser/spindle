"""`Stage` protocol (architecture.md §1) plus `IdentifyStage`, the first
stage in `TaskKind`'s pipeline order.

Only `IdentifyStage` exists yet — rip/transcode/subtitles/organize/notify
land in their own phases. Nothing dispatches stages automatically yet either
(that's the phase 3/4 `Dispatcher`); `DriveWatcher` currently runs the same
`identify_disc` orchestration eagerly at insert-time so a job gets a title
without waiting on a dispatcher that doesn't exist yet. This stage exists so
that dispatcher has a documented, tested `Stage` to run identify through
(e.g. on a manual retry) once it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from spindle.adapters.tmdb import TmdbAdapter
from spindle.models.job import JobStatus
from spindle.models.task import TaskKind, TaskStatus
from spindle.pipeline.context import JobContext
from spindle.services.identifier import identify_disc
from spindle.utils.naming import normalize_disc_label


@dataclass
class StageResult:
    status: TaskStatus
    artifacts: list[Path] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


class Stage(Protocol):
    kind: ClassVar[TaskKind]

    async def run(self, ctx: JobContext) -> StageResult: ...
    async def can_skip(self, ctx: JobContext) -> bool: ...
    async def cleanup(self, ctx: JobContext) -> None: ...


class IdentifyStage:
    kind: ClassVar[TaskKind] = TaskKind.IDENTIFY

    def __init__(self, tmdb: TmdbAdapter) -> None:
        self._tmdb = tmdb

    async def can_skip(self, ctx: JobContext) -> bool:
        return ctx.job.tmdb_id is not None

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        label = ctx.disc.label if ctx.disc is not None else None
        normalized_title, hints = normalize_disc_label(label or job.title or "")

        if not normalized_title:
            job.status = JobStatus.NEEDS_REVIEW
            return StageResult(
                status=TaskStatus.SUCCEEDED,
                meta={"accepted": False},
                message="No usable disc label; parked for review.",
            )

        outcome = await identify_disc(
            self._tmdb,
            normalized_title=normalized_title,
            hints=hints,
            disc_runtime_minutes=None,
            confidence_threshold=ctx.config.behaviour.identify_confidence_threshold,
        )

        job.media_kind = outcome.media_kind
        job.title = outcome.title or normalized_title
        job.original_title = outcome.original_title
        job.year = outcome.year
        job.season = hints.season
        job.tmdb_id = outcome.tmdb_id
        job.confidence = outcome.confidence
        job.poster_url = outcome.poster_url
        if outcome.candidates:
            job.meta = {**job.meta, "candidates": outcome.candidates}

        if not outcome.accepted:
            job.status = JobStatus.NEEDS_REVIEW
            return StageResult(
                status=TaskStatus.SUCCEEDED,
                meta={"accepted": False},
                message="Below confidence threshold; parked for review.",
            )

        return StageResult(status=TaskStatus.SUCCEEDED, meta={"accepted": True})

    async def cleanup(self, ctx: JobContext) -> None:
        return None
