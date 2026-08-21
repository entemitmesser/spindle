"""`Stage` protocol (architecture.md §1) plus the pipeline stages that exist
so far: `IdentifyStage` and `RipStage`.

Nothing dispatches stages automatically yet (that's phase 4's `Dispatcher`);
`DriveWatcher` runs `identify_disc` eagerly at insert-time so a job gets a
title without waiting on a dispatcher that doesn't exist yet. `RipStage` is
fully implemented and tested against a `JobContext` built by hand, ready for
the dispatcher to run once it lands.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from spindle.adapters.ffmpeg import FfmpegAdapter
from spindle.adapters.handbrake import HandBrakeAdapter, HandBrakeError
from spindle.adapters.makemkv import MakeMkvAdapter, MakeMkvError, RipProgress
from spindle.adapters.optical import OpticalAdapter
from spindle.adapters.tmdb import TmdbAdapter
from spindle.errors import AdapterError
from spindle.models.drive import DriveStatus
from spindle.models.job import JobStatus
from spindle.models.task import TaskKind, TaskStatus
from spindle.pipeline.context import JobContext
from spindle.services.events import EventIn
from spindle.services.identifier import identify_disc
from spindle.services.ripper import TitleCandidate, TitleSelectionResult, select_titles
from spindle.utils import fsutil
from spindle.utils.naming import normalize_disc_label
from spindle.utils.progress import Throttler


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


_VERIFY_DURATION_TOLERANCE = 0.02  # 2%, same tolerance select_titles uses for confidence
_PREFLIGHT_HEADROOM = 1.15


@dataclass(frozen=True)
class _ScannedTitle:
    title_id: int
    duration_seconds: int
    chapter_count: int
    size_bytes: int


@dataclass(frozen=True)
class _ScanResult:
    backend: str  # "makemkv" | "handbrake"
    titles: list[_ScannedTitle]


@dataclass(frozen=True)
class _Plan:
    backend: str
    selection: TitleSelectionResult


def _output_name(index: int, total: int) -> str:
    return "title.mkv" if total == 1 else f"title-{index:02d}.mkv"


def _duration_matches(probed_seconds: float | None, expected_seconds: int) -> bool:
    if probed_seconds is None or expected_seconds <= 0:
        return False
    return abs(probed_seconds - expected_seconds) <= expected_seconds * _VERIFY_DURATION_TOLERANCE


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class _RateEstimator:
    """Rolling bytes-written rate for ETA, sampled against the output file
    on disk rather than the progress percentage — MakeMKV's own progress
    stalls on slow network shares while bytes keep landing (phase doc
    gotcha)."""

    def __init__(self) -> None:
        self._first: tuple[float, int] | None = None

    def eta_seconds(self, current_bytes: int, expected_bytes: int) -> int | None:
        now = time.monotonic()
        if self._first is None:
            self._first = (now, current_bytes)
            return None
        first_time, first_bytes = self._first
        elapsed = now - first_time
        written = current_bytes - first_bytes
        if elapsed <= 0 or written <= 0 or expected_bytes <= 0:
            return None
        rate = written / elapsed
        remaining = max(expected_bytes - current_bytes, 0)
        return int(remaining / rate)


class RipStage:
    """Scan -> select -> rip -> verify, per phase-03-rip-engine.md task 4.

    MakeMKV is the only backend that can actually rip in this phase (see the
    phase's design note: HandBrake direct-from-disc re-encodes and needs
    `HandBrakeAdapter.encode()`, which doesn't land until phase 4). When the
    configured/available backend resolves to HandBrake, title *selection*
    still runs against its scan so the job's `meta["titles"]` is useful, but
    the rip itself fails with a clear, actionable message instead of a
    stack trace.
    """

    kind: ClassVar[TaskKind] = TaskKind.RIP

    def __init__(
        self,
        makemkv: MakeMkvAdapter,
        handbrake: HandBrakeAdapter,
        ffmpeg: FfmpegAdapter,
        optical: OpticalAdapter,
    ) -> None:
        self._makemkv = makemkv
        self._handbrake = handbrake
        self._ffmpeg = ffmpeg
        self._optical = optical
        # Keyed by job id: the output path currently being written, so
        # `cleanup()` knows what to delete without touching already-ripped
        # sibling titles from the same job.
        self._active_output: dict[int, Path] = {}

    async def can_skip(self, ctx: JobContext) -> bool:
        if ctx.job.id is None:
            return False
        plan = await self._plan(ctx)
        if plan is None or plan.backend != "makemkv":
            return False
        if plan.selection.needs_review or not plan.selection.selected:
            return False

        raw = fsutil.raw_dir(ctx.work_dir, ctx.job.id)
        total = len(plan.selection.selected)
        for index, candidate in enumerate(plan.selection.selected, start=1):
            probe = await self._ffmpeg.probe(raw / _output_name(index, total))
            probed_seconds = probe.duration_seconds if probe is not None else None
            if not _duration_matches(probed_seconds, candidate.duration_seconds):
                return False
        return True

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        if job.id is None or ctx.drive is None or ctx.disc is None:
            return StageResult(
                status=TaskStatus.FAILED, message="No drive/disc available to rip from."
            )

        device = ctx.drive.device_path
        raw = fsutil.raw_dir(ctx.work_dir, job.id)

        try:
            plan = await self._build_plan(ctx, device)
        except (MakeMkvError, HandBrakeError) as exc:
            return StageResult(
                status=TaskStatus.FAILED, message=f"Could not scan the disc: {exc.message}"
            )

        job.meta = {**job.meta, "titles": list(plan.selection.candidates_meta)}

        if plan.selection.needs_review or not plan.selection.selected:
            job.status = JobStatus.NEEDS_REVIEW
            return StageResult(
                status=TaskStatus.SUCCEEDED,
                meta={"accepted": False},
                message=plan.selection.reason or "Title selection needs review.",
            )

        if plan.backend != "makemkv":
            return StageResult(
                status=TaskStatus.FAILED,
                message="MakeMKV isn't installed; HandBrake fallback ripping isn't available yet.",
            )

        expected_bytes = max((c.size_bytes for c in plan.selection.selected), default=0)
        if not await self._has_enough_free_space(ctx, expected_bytes):
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=job.id,
                    drive_id=ctx.drive.id,
                    data={
                        "code": "low_disk_space",
                        "message": "Not enough free space on the work volume to rip this disc.",
                    },
                )
            )
            return StageResult(
                status=TaskStatus.FAILED, message="Not enough free space to rip this disc."
            )

        ctx.drive.status = DriveStatus.BUSY
        raw.mkdir(parents=True, exist_ok=True)

        artifacts: list[Path] = []
        media_files: list[dict[str, Any]] = []
        total = len(plan.selection.selected)
        throttler = Throttler(1.0)

        try:
            for index, candidate in enumerate(plan.selection.selected, start=1):
                dest = raw / _output_name(index, total)
                self._active_output[job.id] = dest
                rate = _RateEstimator()

                async def on_progress(
                    progress: RipProgress,
                    *,
                    _dest: Path = dest,
                    _rate: _RateEstimator = rate,
                    _candidate: TitleCandidate = candidate,
                    _index: int = index,
                ) -> None:
                    if not throttler.should_emit():
                        return
                    eta = _rate.eta_seconds(_file_size(_dest), _candidate.size_bytes)
                    await ctx.emit(
                        EventIn(
                            type="task.progress",
                            job_id=job.id,
                            data={
                                "kind": TaskKind.RIP.value,
                                "progress": progress.progress,
                                "eta_seconds": eta,
                                "message": progress.message or f"Title {_index} of {total}",
                            },
                        )
                    )

                ripped_path = await self._makemkv.rip_title(
                    device, candidate.title_id, raw, on_progress, handle=job.id
                )
                self._active_output.pop(job.id, None)
                if ripped_path != dest:
                    ripped_path.replace(dest)

                probe = await self._ffmpeg.probe(dest)
                if probe is None or not _duration_matches(
                    probe.duration_seconds, candidate.duration_seconds
                ):
                    dest.unlink(missing_ok=True)
                    await self._recover_drive(ctx)
                    return StageResult(
                        status=TaskStatus.FAILED,
                        message=(
                            f"Ripped output for title {candidate.title_id} failed verification."
                        ),
                    )

                artifacts.append(dest)
                duration = int(probe.duration_seconds or candidate.duration_seconds)
                media_files.append(
                    {
                        "path": str(dest),
                        "title_id": candidate.title_id,
                        "role": "source",
                        "size_bytes": probe.size_bytes,
                        "duration_seconds": duration,
                    }
                )
        except MakeMkvError as exc:
            await self._recover_drive(ctx)
            return StageResult(status=TaskStatus.FAILED, message=f"Rip failed: {exc.message}")
        finally:
            self._active_output.pop(job.id, None)

        await self._recover_drive(ctx)
        return StageResult(
            status=TaskStatus.SUCCEEDED,
            artifacts=artifacts,
            meta={"accepted": True, "media_files": media_files},
        )

    async def cleanup(self, ctx: JobContext) -> None:
        job_id = ctx.job.id
        if job_id is None:
            return
        await self._makemkv.cancel(job_id)
        pending = self._active_output.pop(job_id, None)
        if pending is not None:
            pending.unlink(missing_ok=True)
        await self._recover_drive(ctx)

    # -- internals -----------------------------------------------------

    async def _plan(self, ctx: JobContext) -> _Plan | None:
        if ctx.drive is None:
            return None
        try:
            return await self._build_plan(ctx, ctx.drive.device_path)
        except (MakeMkvError, HandBrakeError):
            return None

    async def _build_plan(self, ctx: JobContext, device: str) -> _Plan:
        scan = await self._scan(ctx, device)
        candidates = [
            TitleCandidate(
                title_id=t.title_id,
                duration_seconds=t.duration_seconds,
                chapter_count=t.chapter_count,
                size_bytes=t.size_bytes,
            )
            for t in scan.titles
        ]
        selection = select_titles(candidates, config=ctx.config.rip, media_kind=ctx.job.media_kind)
        return _Plan(backend=scan.backend, selection=selection)

    async def _scan(self, ctx: JobContext, device: str) -> _ScanResult:
        if ctx.config.rip.backend == "makemkv" and await self._makemkv.is_available():
            disc_scan = await self._makemkv.scan(device)
            titles = [
                _ScannedTitle(t.title_id, t.duration_seconds, t.chapter_count, t.size_bytes)
                for t in disc_scan.titles
            ]
            return _ScanResult(backend="makemkv", titles=titles)

        hb_scan = await self._handbrake.scan_disc(device)
        titles = [
            _ScannedTitle(t.title_id, t.duration_seconds, t.chapter_count, 0)
            for t in hb_scan.titles
        ]
        return _ScanResult(backend="handbrake", titles=titles)

    async def _has_enough_free_space(self, ctx: JobContext, expected_bytes: int) -> bool:
        usage = await asyncio.to_thread(shutil.disk_usage, ctx.work_dir)
        headroom = int(expected_bytes * _PREFLIGHT_HEADROOM)
        required = max(headroom, ctx.config.behaviour.min_free_bytes)
        return usage.free >= required

    async def _recover_drive(self, ctx: JobContext) -> None:
        if ctx.drive is None:
            return
        if ctx.config.behaviour.auto_eject:
            with contextlib.suppress(AdapterError):
                await asyncio.to_thread(self._optical.eject, ctx.drive.device_path)
        ctx.drive.status = DriveStatus.EMPTY
        ctx.drive.current_job_id = None
