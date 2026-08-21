"""`Stage` protocol (architecture.md §1) plus the pipeline stages that exist
so far: `IdentifyStage`, `RipStage`, `TranscodeStage` and `SubtitleStage`.

Nothing dispatches stages automatically except phase 4's `Dispatcher`
(`spindle/services/queue.py`), which isn't wired into `main.py` yet —
`DriveWatcher` still runs `identify_disc` eagerly at insert-time. Every stage
here is fully implemented and tested against a `JobContext` built by hand,
ready for the dispatcher (or a future `main.py` wiring) to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol

from spindle.adapters.ffmpeg import FfmpegAdapter, MediaProbe
from spindle.adapters.handbrake import EncodeProgress, HandBrakeAdapter, HandBrakeError
from spindle.adapters.jellyfin import JellyfinAdapter
from spindle.adapters.makemkv import MakeMkvAdapter, MakeMkvError, RipProgress
from spindle.adapters.mkvpropedit import TrackEdit
from spindle.adapters.opensubtitles import OpenSubtitlesAdapter, OpenSubtitlesQuotaExceededError
from spindle.adapters.optical import OpticalAdapter
from spindle.adapters.tmdb import TmdbAdapter
from spindle.errors import AdapterError, ValidationFailedError
from spindle.models.disc import DiscType
from spindle.models.drive import DriveStatus
from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.pipeline.context import JobContext
from spindle.presets import find_preset
from spindle.services import subtitles as subtitles_service
from spindle.services.events import EventIn
from spindle.services.identifier import identify_disc
from spindle.services.metadata import MetadataService
from spindle.services.notifier import NotificationService
from spindle.services.ripper import TitleCandidate, TitleSelectionResult, select_titles
from spindle.services.transcoder import (
    build_overrides,
    choose_encoder,
    remap_quality,
    resolve_preset_name,
)
from spindle.utils import fsutil, naming
from spindle.utils import lang as langutil
from spindle.utils.hashing import opensubtitles_hash
from spindle.utils.naming import normalize_disc_label
from spindle.utils.progress import Throttler


def _safe_lang(code: str | None, normalize: Callable[[str], str]) -> str | None:
    """Best-effort language normalization for values that came off a disc or
    a third-party API — an unrecognized code shouldn't blow up a stage
    (CLAUDE.md rule 7), it should just pass through un-normalized."""
    if not code:
        return None
    try:
        return normalize(code)
    except langutil.UnknownLanguageError:
        return code


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


# Whole-job progress weighting, per phase-07-api-realtime.md task 1:
# `JobSummary.progress` weights each `TaskKind` by how much of the pipeline's
# wall-clock time it typically takes, so the dashboard bar moves sensibly
# instead of jumping a flat 1/6 per stage regardless of how long each one runs.
STAGE_WEIGHTS: Final[dict[TaskKind, float]] = {
    TaskKind.IDENTIFY: 0.02,
    TaskKind.RIP: 0.45,
    TaskKind.TRANSCODE: 0.45,
    TaskKind.SUBTITLES: 0.03,
    TaskKind.ORGANIZE: 0.04,
    TaskKind.NOTIFY: 0.01,
}


def job_progress(tasks: Iterable[Task]) -> float:
    """Whole-job progress in `0.0-1.0`: each task contributes its full
    `STAGE_WEIGHTS` share once terminal (succeeded/skipped), or a fraction of
    it while running, based on the task's own `progress`."""
    total = 0.0
    for task in tasks:
        weight = STAGE_WEIGHTS.get(task.kind, 0.0)
        if task.status in (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED):
            total += weight
        elif task.status == TaskStatus.RUNNING:
            total += weight * max(0.0, min(1.0, task.progress))
    return max(0.0, min(1.0, total))


def current_task_kind(tasks: Iterable[Task]) -> TaskKind | None:
    """The task the job is (or would next be) working on: the first one not
    already terminal-success, in pipeline order."""
    for task in tasks:
        if task.status not in (TaskStatus.SUCCEEDED, TaskStatus.SKIPPED):
            return task.kind
    return None


def current_task_eta_seconds(tasks: Iterable[Task]) -> int | None:
    for task in tasks:
        if task.status == TaskStatus.RUNNING:
            return task.eta_seconds
    return None


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


_TRANSCODE_MIN_SIZE_RATIO = 0.05  # of source, per phase-04 task 4
_TRANSCODE_PREFLIGHT_HEADROOM_RATIO = 0.6
_TRANSCODE_PREFLIGHT_MIN_BYTES = 10 * 1024**3


@dataclass(frozen=True)
class _SourceFile:
    path: Path
    size_bytes: int
    duration_seconds: float | None


def _progress_message(encoder: str, progress: EncodeProgress, index: int, total: int) -> str:
    parts = [encoder]
    if progress.fps is not None:
        parts.append(f"{progress.fps:.0f} fps")
    if total > 1:
        parts.append(f"file {index} of {total}")
    return " · ".join(parts)


def _format_source_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    return f"{int(seconds) // 60} min"


class TranscodeStage:
    """HandBrake encode -> verify, per phase-04-transcode-engine.md task 4.

    Reads whatever `RipStage` left in `work/raw/job-X/` (one or more `*.mkv`
    files) rather than a DB-backed media file list — `RipStage` doesn't
    persist one either (see its docstring above); turning
    `StageResult.meta["media_files"]` into `MediaFile` rows is a future
    dispatcher/organizer concern.
    """

    kind: ClassVar[TaskKind] = TaskKind.TRANSCODE

    def __init__(
        self,
        handbrake: HandBrakeAdapter,
        ffmpeg: FfmpegAdapter,
        *,
        user_preset_dir: Path | None = None,
    ) -> None:
        self._handbrake = handbrake
        self._ffmpeg = ffmpeg
        self._user_preset_dir = user_preset_dir
        # Keyed by job id: the output path currently being written, same
        # cancel/cleanup bookkeeping as `RipStage._active_output`.
        self._active_output: dict[int, Path] = {}

    async def can_skip(self, ctx: JobContext) -> bool:
        if ctx.job.id is None or not ctx.config.transcode.enabled:
            return False
        sources = await self._source_files(ctx)
        if not sources:
            return False
        out_dir = fsutil.transcode_dir(ctx.work_dir, ctx.job.id)
        for src in sources:
            if not await self._verify_output(out_dir / src.path.name, src):
                return False
        return True

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        if job.id is None:
            return StageResult(status=TaskStatus.FAILED, message="Job has no id.")

        sources = await self._source_files(ctx)
        if not sources:
            return StageResult(
                status=TaskStatus.FAILED, message="No ripped source files to transcode."
            )

        if not ctx.config.transcode.enabled:
            job.meta = {**job.meta, "encoder": "passthrough"}
            passthrough_files = [
                {
                    "path": str(src.path),
                    "role": "transcoded",
                    "size_bytes": src.size_bytes,
                    "duration_seconds": int(src.duration_seconds or 0),
                }
                for src in sources
            ]
            return StageResult(
                status=TaskStatus.SUCCEEDED,
                artifacts=[src.path for src in sources],
                meta={"accepted": True, "media_files": passthrough_files},
            )

        total_source_bytes = sum(src.size_bytes for src in sources)
        if not await self._has_enough_free_space(ctx, total_source_bytes):
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=job.id,
                    drive_id=ctx.drive.id if ctx.drive is not None else None,
                    data={
                        "code": "low_disk_space",
                        "message": (
                            "Not enough free space on the work volume to transcode this job."
                        ),
                    },
                )
            )
            return StageResult(
                status=TaskStatus.FAILED, message="Not enough free space to transcode."
            )

        disc_type = ctx.disc.disc_type if ctx.disc is not None else None
        preset_name = resolve_preset_name(disc_type, ctx.config.transcode)
        preset = find_preset(preset_name, self._user_preset_dir)
        if preset is None:
            return StageResult(
                status=TaskStatus.FAILED, message=f"Unknown transcode preset {preset_name!r}."
            )

        available_encoders = await self._handbrake.available_encoders()
        encoder = choose_encoder(ctx.config.transcode, available_encoders, preset_name)
        quality = remap_quality(encoder, ctx.config.transcode.quality)
        overrides = build_overrides(
            ctx.config.transcode,
            encoder,
            quality,
            keep_forced_subs=ctx.config.subtitles.keep_forced_from_disc,
        )

        out_dir = fsutil.transcode_dir(ctx.work_dir, job.id)
        out_dir.mkdir(parents=True, exist_ok=True)

        weights = [
            src.duration_seconds if src.duration_seconds and src.duration_seconds > 0 else 1.0
            for src in sources
        ]
        total_weight = sum(weights) or 1.0
        elapsed_weight = 0.0
        total = len(sources)
        throttler = Throttler(1.0)

        artifacts: list[Path] = []
        media_files: list[dict[str, Any]] = []

        try:
            for index, src in enumerate(sources, start=1):
                dest = out_dir / src.path.name
                self._active_output[job.id] = dest
                weight = weights[index - 1]
                base_weight = elapsed_weight

                async def on_progress(
                    progress: EncodeProgress,
                    *,
                    _index: int = index,
                    _weight: float = weight,
                    _base: float = base_weight,
                ) -> None:
                    if not throttler.should_emit():
                        return
                    overall = (_base + _weight * progress.progress) / total_weight
                    await ctx.emit(
                        EventIn(
                            type="task.progress",
                            job_id=job.id,
                            data={
                                "kind": TaskKind.TRANSCODE.value,
                                "progress": min(1.0, max(0.0, overall)),
                                "eta_seconds": progress.eta_seconds,
                                "message": _progress_message(encoder, progress, _index, total),
                            },
                        )
                    )

                await self._handbrake.encode(
                    src.path,
                    dest,
                    preset_file=preset.file,
                    preset_name=preset_name,
                    overrides=overrides,
                    on_progress=on_progress,
                    handle=job.id,
                )
                self._active_output.pop(job.id, None)

                if not await self._verify_output(dest, src):
                    dest.unlink(missing_ok=True)
                    job.status = JobStatus.NEEDS_REVIEW
                    return StageResult(
                        status=TaskStatus.SUCCEEDED,
                        meta={"accepted": False},
                        message=(
                            f"Transcode output for {src.path.name} looks wrong "
                            f"(source duration {_format_source_duration(src.duration_seconds)})."
                            " Check the log."
                        ),
                    )

                probe = await self._ffmpeg.probe(dest)
                assert probe is not None  # noqa: S101 -- _verify_output already confirmed this
                artifacts.append(dest)
                media_files.append(
                    {
                        "path": str(dest),
                        "role": "transcoded",
                        "size_bytes": probe.size_bytes,
                        "duration_seconds": int(probe.duration_seconds or 0),
                        "video_codec": probe.video_codec,
                    }
                )
                # Forced disc subtitles survive the encode as soft tracks
                # (task 1) — record each one so `SubtitleStage` knows what's
                # already embedded and doesn't fetch a duplicate.
                for stream in probe.subtitle_streams:
                    media_files.append(
                        {
                            "path": str(dest),
                            "role": "subtitle",
                            "language": _safe_lang(stream.language, langutil.to_639_2t),
                            "is_forced": stream.is_forced,
                            "meta": {
                                "stream_index": stream.index,
                                "codec": stream.codec,
                                "muxed": True,
                            },
                        }
                    )
                elapsed_weight += weight
        except HandBrakeError as exc:
            return StageResult(status=TaskStatus.FAILED, message=f"Transcode failed: {exc.message}")
        finally:
            self._active_output.pop(job.id, None)

        if not ctx.config.rip.keep_raw_after_transcode:
            shutil.rmtree(fsutil.raw_dir(ctx.work_dir, job.id), ignore_errors=True)

        job.meta = {**job.meta, "encoder": encoder}
        return StageResult(
            status=TaskStatus.SUCCEEDED,
            artifacts=artifacts,
            meta={"accepted": True, "media_files": media_files},
        )

    async def cleanup(self, ctx: JobContext) -> None:
        job_id = ctx.job.id
        if job_id is None:
            return
        await self._handbrake.cancel(job_id)
        pending = self._active_output.pop(job_id, None)
        if pending is not None:
            pending.unlink(missing_ok=True)

    # -- internals -----------------------------------------------------

    async def _source_files(self, ctx: JobContext) -> list[_SourceFile]:
        if ctx.job.id is None:
            return []
        raw = fsutil.raw_dir(ctx.work_dir, ctx.job.id)
        if not raw.is_dir():
            return []
        sources = []
        for path in sorted(raw.glob("*.mkv")):
            probe = await self._ffmpeg.probe(path)
            sources.append(
                _SourceFile(
                    path=path,
                    size_bytes=probe.size_bytes if probe is not None else _file_size(path),
                    duration_seconds=probe.duration_seconds if probe is not None else None,
                )
            )
        return sources

    async def _verify_output(self, dest: Path, src: _SourceFile) -> bool:
        probe = await self._ffmpeg.probe(dest)
        if probe is None or not probe.has_video:
            return False
        if src.duration_seconds is not None and not _duration_matches(
            probe.duration_seconds, int(src.duration_seconds)
        ):
            return False
        min_size = src.size_bytes * _TRANSCODE_MIN_SIZE_RATIO
        return not (src.size_bytes > 0 and probe.size_bytes < min_size)

    async def _has_enough_free_space(self, ctx: JobContext, total_source_bytes: int) -> bool:
        usage = await asyncio.to_thread(shutil.disk_usage, ctx.work_dir)
        required = max(
            int(total_source_bytes * _TRANSCODE_PREFLIGHT_HEADROOM_RATIO),
            _TRANSCODE_PREFLIGHT_MIN_BYTES,
        )
        return usage.free >= required


class SubtitleStage:
    """External subtitles + container tagging, per
    phase-05-metadata-subtitles.md tasks 2-4.

    There's no separate `TaskKind` for "tag" (the fixed order in CLAUDE.md §4
    only adds `SUBTITLES` this phase), so tagging — `mkvpropedit`, TMDB
    artwork, the optional NFO — happens here too, right before `ORGANIZE`.
    Per task 3, this stage's failure mode is always `SKIPPED` with a message,
    never `FAILED`: a job should complete with worse subtitles, not park in
    `needs_review` because a third-party API had a bad day.
    """

    kind: ClassVar[TaskKind] = TaskKind.SUBTITLES

    def __init__(
        self,
        opensubtitles: OpenSubtitlesAdapter,
        ffmpeg: FfmpegAdapter,
        metadata: MetadataService,
    ) -> None:
        self._opensubtitles = opensubtitles
        self._ffmpeg = ffmpeg
        self._metadata = metadata

    async def can_skip(self, ctx: JobContext) -> bool:
        if not ctx.config.subtitles.enabled:
            return True
        if ctx.job.id is None:
            return False
        sources = await self._transcoded_files(ctx)
        if not sources:
            return False
        embedded = await self._embedded_full_langs(sources)
        wanted = subtitles_service.wanted_languages(ctx.config.subtitles.langs, embedded)
        return not wanted

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        if job.id is None:
            return StageResult(status=TaskStatus.FAILED, message="Job has no id.")

        if not ctx.config.subtitles.enabled:
            return StageResult(status=TaskStatus.SKIPPED, message="Subtitles disabled in config.")

        sources = await self._transcoded_files(ctx)
        if not sources:
            return StageResult(status=TaskStatus.FAILED, message="No transcoded files to caption.")

        media_files: list[dict[str, Any]] = []
        await self._tag_sources(ctx, sources, media_files)

        no_provider = (
            ctx.config.subtitles.external_provider == "none"
            or not self._opensubtitles.is_configured
        )
        if no_provider:
            return StageResult(
                status=TaskStatus.SKIPPED,
                meta={"media_files": media_files} if media_files else {},
                message="No subtitle provider configured",
            )

        embedded = await self._embedded_full_langs(sources)
        wanted = subtitles_service.wanted_languages(ctx.config.subtitles.langs, embedded)
        if not wanted:
            return StageResult(
                status=TaskStatus.SKIPPED,
                meta={"media_files": media_files} if media_files else {},
                message="All wanted languages already embedded.",
            )

        fetched_any = False
        skipped_langs: list[str] = []
        for src in sources:
            moviehash = await asyncio.to_thread(opensubtitles_hash, src)
            for target_lang in wanted:
                if await self._fetch_one(ctx, src, moviehash, target_lang, media_files):
                    fetched_any = True
                else:
                    skipped_langs.append(target_lang)

        if not fetched_any:
            return StageResult(
                status=TaskStatus.SKIPPED,
                meta={"media_files": media_files} if media_files else {},
                message="No subtitles found for the wanted languages.",
            )

        message = None
        if skipped_langs:
            message = f"Some languages unavailable: {', '.join(sorted(set(skipped_langs)))}"
        return StageResult(
            status=TaskStatus.SUCCEEDED,
            meta={"accepted": True, "media_files": media_files},
            message=message,
        )

    async def cleanup(self, ctx: JobContext) -> None:
        return None

    # -- internals -----------------------------------------------------

    async def _fetch_one(
        self,
        ctx: JobContext,
        src: Path,
        moviehash: str,
        target_lang: str,
        media_files: list[dict[str, Any]],
    ) -> bool:
        try:
            opensubtitles_lang = langutil.to_639_1(target_lang)
        except langutil.UnknownLanguageError:
            return False

        try:
            results = await self._opensubtitles.search(
                languages=[opensubtitles_lang],
                moviehash=moviehash,
                tmdb_id=ctx.job.tmdb_id,
                year=ctx.job.year,
                query=None if ctx.job.tmdb_id else ctx.job.title,
            )
        except OpenSubtitlesQuotaExceededError as exc:
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=ctx.job.id,
                    data={"code": "opensubtitles_quota", "message": exc.message},
                )
            )
            return False
        except AdapterError:
            return False

        candidates = _parse_candidates(results, target_lang)
        ranked = subtitles_service.rank_candidates(
            candidates, hearing_impaired=ctx.config.subtitles.hearing_impaired
        )
        for candidate in ranked:
            if await self._try_download(ctx, src, target_lang, candidate, media_files):
                return True
        return False

    async def _try_download(
        self,
        ctx: JobContext,
        src: Path,
        target_lang: str,
        candidate: subtitles_service.SubtitleCandidate,
        media_files: list[dict[str, Any]],
    ) -> bool:
        try:
            raw = await self._opensubtitles.download(candidate.file_id)
        except AdapterError:
            return False

        text = subtitles_service.decode_subtitle_bytes(raw)
        validation = subtitles_service.validate_srt(text)
        if not validation.ok:
            ctx.logger.info("subtitles.rejected", language=target_lang, reason=validation.reason)
            return False

        modifier = ".sdh" if candidate.is_hearing_impaired else ""
        dest = src.with_name(f"{src.stem}.{target_lang}{modifier}.srt")
        dest.write_text(text, encoding="utf-8")
        media_files.append(
            {
                "path": str(dest),
                "role": "subtitle",
                "language": target_lang,
                "is_forced": False,
                "meta": {
                    "source": "opensubtitles",
                    "hearing_impaired": candidate.is_hearing_impaired,
                },
            }
        )
        return True

    async def _tag_sources(
        self, ctx: JobContext, sources: list[Path], media_files: list[dict[str, Any]]
    ) -> None:
        job = ctx.job
        assert job.id is not None  # noqa: S101 -- checked by run()/can_skip() callers

        for src in sources:
            probe = await self._ffmpeg.probe(src)
            tracks = (
                [
                    TrackEdit(
                        track_id=i,
                        track_type="s",
                        language=_safe_lang(stream.language, langutil.to_639_2b),
                        is_forced=stream.is_forced,
                    )
                    for i, stream in enumerate(probe.subtitle_streams, start=1)
                ]
                if probe is not None
                else []
            )
            with contextlib.suppress(AdapterError):
                await self._metadata.tag(src, job=job, tracks=tracks)

        dest_dir = fsutil.transcode_dir(ctx.work_dir, job.id)
        artwork = await self._metadata.fetch_artwork(job, dest_dir)
        for path in artwork:
            media_files.append({"path": str(path), "role": "artwork"})

        with contextlib.suppress(AdapterError):
            await self._metadata.write_nfo(
                job, sources[0].with_suffix(".nfo"), config=ctx.config.library
            )

    async def _embedded_full_langs(self, sources: list[Path]) -> set[str]:
        langs: set[str] = set()
        for src in sources:
            probe = await self._ffmpeg.probe(src)
            if probe is None:
                continue
            for stream in probe.subtitle_streams:
                if stream.is_forced:
                    continue
                normalized = _safe_lang(stream.language, langutil.to_639_2t)
                if normalized is not None:
                    langs.add(normalized)
        return langs

    async def _transcoded_files(self, ctx: JobContext) -> list[Path]:
        if ctx.job.id is None:
            return []
        out_dir = fsutil.transcode_dir(ctx.work_dir, ctx.job.id)
        if not out_dir.is_dir():
            return []
        return sorted(out_dir.glob("*.mkv"))


def _parse_candidates(
    results: list[dict[str, Any]], target_lang: str
) -> list[subtitles_service.SubtitleCandidate]:
    candidates = []
    for item in results:
        attrs = item.get("attributes") or {}
        files = attrs.get("files") or []
        if not files:
            continue
        file_id = files[0].get("file_id")
        if file_id is None:
            continue
        candidates.append(
            subtitles_service.SubtitleCandidate(
                file_id=int(file_id),
                language=target_lang,
                release=attrs.get("release"),
                download_count=int(attrs.get("download_count") or 0),
                moviehash_match=bool(attrs.get("moviehash_match")),
                is_hearing_impaired=bool(attrs.get("hearing_impaired")),
                ai_translated=bool(attrs.get("ai_translated")),
                machine_translated=bool(attrs.get("machine_translated")),
            )
        )
    return candidates


# -- OrganizeStage, per phase-06-library-jellyfin.md tasks 1-3 --------------

_ORGANIZE_COPY_CHUNK_BYTES = 1024 * 1024  # 1 MiB, per phase doc task 2
_ORGANIZE_PREFLIGHT_HEADROOM = 1.05
_ORGANIZE_ARTWORK_NAMES = ("poster.jpg", "fanart.jpg")

_SOURCE_LABELS: dict[DiscType, str] = {
    DiscType.DVD: "DVD",
    DiscType.BLURAY: "Bluray",
    DiscType.UHD_BLURAY: "UHD",
}


def _resolution_label(width: int | None, height: int | None) -> str | None:
    if height is None:
        return None
    if height >= 2000:
        return "2160p"
    if height >= 1000:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 576:
        return "576p"
    if height >= 480:
        return "480p"
    return None


def _naming_variables(
    job: Job, probe: MediaProbe | None, disc_type: DiscType | None
) -> dict[str, Any]:
    """Always supplies every `naming.NAMING_VARIABLES` key (empty/0 when the
    job has no value for it) so the restricted formatter's "variable has no
    value here" check never fires for a real job -- only for a naming.py unit
    test that deliberately passes a partial dict."""
    meta = job.meta or {}
    edition = meta.get("edition")
    episode = meta.get("episode")
    episode_title = meta.get("episode_title")
    source = _SOURCE_LABELS.get(disc_type) if disc_type is not None else None
    resolution = _resolution_label(probe.width, probe.height) if probe is not None else None
    return {
        "title": job.title or "Untitled",
        "original_title": job.original_title or job.title or "Untitled",
        "year": job.year or 0,
        "season": job.season or 0,
        "episode": episode if isinstance(episode, int) else 0,
        "episode_title": episode_title if isinstance(episode_title, str) else "",
        "tmdb_id": job.tmdb_id or 0,
        "imdb_id": job.imdb_id or "",
        "source": source or "",
        "resolution": resolution or "",
        "video_codec": (probe.video_codec if probe is not None else None) or "",
        "audio_codec": (probe.audio_codec if probe is not None else None) or "",
        "edition": edition if isinstance(edition, str) else "",
    }


def _next_available_name(dest: Path) -> Path:
    """`keep_both` collision naming: `dest` itself if free, else ` - 2`, ` - 3`, ..."""
    if not dest.exists():
        return dest
    n = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} - {n}{dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _same_device(src: Path, dest: Path) -> bool:
    try:
        return src.stat().st_dev == dest.parent.stat().st_dev
    except OSError:
        return False


def _blocking_copy_with_fsync(src: Path, dest: Path, chunk_size: int) -> None:
    """The "write the copy loop yourself" gotcha: `shutil.move` across
    devices is neither atomic nor fsync'd. Runs under `asyncio.to_thread`
    (CLAUDE.md rule 4) -- this is a blocking function, never awaited directly."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fsrc, dest.open("wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, chunk_size)
        fdst.flush()
        os.fsync(fdst.fileno())


@dataclass(frozen=True)
class _OrganizePlan:
    src_dir: Path
    video_dest_pairs: list[tuple[Path, Path]]


class OrganizeStage:
    """Move transcoded output (plus sidecar subtitles/artwork/NFO) into the
    Jellyfin library, per phase-06-library-jellyfin.md tasks 1-3.

    Like every other stage, this re-derives its inputs from the filesystem
    (`fsutil.transcode_dir`) rather than a DB-backed media file list --
    `TranscodeStage`/`SubtitleStage` don't persist `MediaFile` rows either
    (see their own docstrings); that's a future dispatcher/organizer
    concern this phase still doesn't need, since the stage can glob for
    exactly what it needs (`*.mkv`, matching `*.srt`, `poster.jpg`/
    `fanart.jpg`, `*.nfo`).
    """

    kind: ClassVar[TaskKind] = TaskKind.ORGANIZE

    def __init__(self, ffmpeg: FfmpegAdapter, jellyfin: JellyfinAdapter) -> None:
        self._ffmpeg = ffmpeg
        self._jellyfin = jellyfin

    async def can_skip(self, ctx: JobContext) -> bool:
        try:
            plan = await self._plan(ctx)
        except ValidationFailedError:
            return False
        if plan is None:
            return False
        for video_path, dest in plan.video_dest_pairs:
            if not await self._same_content(video_path, dest):
                return False
        return True

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        if job.id is None:
            return StageResult(status=TaskStatus.FAILED, message="Job has no id.")
        if ctx.library_dir is None or ctx.data_dir is None:
            return StageResult(
                status=TaskStatus.FAILED,
                message="Organize stage is missing a library_dir/data_dir.",
            )

        try:
            plan = await self._plan(ctx)
        except ValidationFailedError as exc:
            return StageResult(status=TaskStatus.FAILED, message=f"Naming template error: {exc}")
        if plan is None:
            return StageResult(status=TaskStatus.FAILED, message="No transcoded files to organize.")

        total_bytes = sum(_file_size(video_path) for video_path, _dest in plan.video_dest_pairs)
        if not await self._has_enough_free_space(ctx, total_bytes):
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=job.id,
                    data={
                        "code": "low_disk_space",
                        "message": (
                            "Not enough free space on the library volume to organize this job."
                        ),
                    },
                )
            )
            return StageResult(
                status=TaskStatus.FAILED, message="Not enough free space on the library volume."
            )

        placed: list[Path] = []
        primary_dest_dir: Path | None = None

        for video_path, dest in plan.video_dest_pairs:
            try:
                placed_dest = await self._place_file(ctx, video_path, dest, role="transcoded")
            except OSError as exc:
                return StageResult(
                    status=TaskStatus.FAILED, message=f"Could not move {video_path.name}: {exc}"
                )
            if placed_dest is None:
                job.status = JobStatus.NEEDS_REVIEW
                return StageResult(
                    status=TaskStatus.SUCCEEDED,
                    meta={"accepted": False},
                    message=(
                        f"Destination already exists and differs: {dest} "
                        f"({_file_size(dest)} bytes) vs new file ({_file_size(video_path)} bytes)."
                    ),
                )
            placed.append(placed_dest)
            if primary_dest_dir is None:
                primary_dest_dir = placed_dest.parent

            original_stem = video_path.stem
            for sub in sorted(plan.src_dir.glob(f"{original_stem}.*.srt")):
                suffix = sub.name[len(original_stem) :]
                sub_dest = placed_dest.with_name(f"{placed_dest.stem}{suffix}")
                with contextlib.suppress(OSError):
                    sub_placed = await self._place_file(ctx, sub, sub_dest, role="subtitle")
                    if sub_placed is not None:
                        placed.append(sub_placed)

            nfo = plan.src_dir / f"{original_stem}.nfo"
            if nfo.exists():
                with contextlib.suppress(OSError):
                    nfo_placed = await self._place_file(
                        ctx, nfo, placed_dest.with_suffix(".nfo"), role="nfo"
                    )
                    if nfo_placed is not None:
                        placed.append(nfo_placed)

        if primary_dest_dir is not None:
            for name in _ORGANIZE_ARTWORK_NAMES:
                art = plan.src_dir / name
                if art.exists():
                    with contextlib.suppress(OSError):
                        art_placed = await self._place_file(
                            ctx, art, primary_dest_dir / name, role="artwork"
                        )
                        if art_placed is not None:
                            placed.append(art_placed)

        job.meta = {
            **job.meta,
            "organize": {"destination": str(primary_dest_dir) if primary_dest_dir else None},
        }

        await self._refresh_jellyfin(ctx, primary_dest_dir)

        shutil.rmtree(plan.src_dir, ignore_errors=True)
        return StageResult(status=TaskStatus.SUCCEEDED, artifacts=placed, meta={"accepted": True})

    async def cleanup(self, ctx: JobContext) -> None:
        return None

    # -- internals -----------------------------------------------------

    async def _plan(self, ctx: JobContext) -> _OrganizePlan | None:
        if ctx.job.id is None or ctx.library_dir is None:
            return None
        src_dir = fsutil.transcode_dir(ctx.work_dir, ctx.job.id)
        if not src_dir.is_dir():
            return None
        video_paths = sorted(src_dir.glob("*.mkv"))
        if not video_paths:
            return None

        disc_type = ctx.disc.disc_type if ctx.disc is not None else None
        lib_cfg = ctx.config.library
        is_series = ctx.job.media_kind == MediaKind.SERIES
        root = ctx.library_dir / (lib_cfg.series_dir if is_series else lib_cfg.movies_dir)
        dir_template = lib_cfg.series_dir_template if is_series else lib_cfg.movie_dir_template
        file_template = lib_cfg.series_file_template if is_series else lib_cfg.movie_file_template

        pairs: list[tuple[Path, Path]] = []
        for index, video_path in enumerate(video_paths, start=1):
            probe = await self._ffmpeg.probe(video_path)
            variables = _naming_variables(ctx.job, probe, disc_type)

            relative_dir = naming.render_path(dir_template, variables)
            if lib_cfg.include_tmdb_id and ctx.job.tmdb_id is not None:
                parts = list(relative_dir.parts)
                parts[0] = f"{parts[0]} [tmdbid-{ctx.job.tmdb_id}]"
                relative_dir = Path(*parts)
            file_stem = naming.sanitize_segment(naming.render_template(file_template, variables))
            if len(video_paths) > 1:
                file_stem = f"{file_stem} - Part{index}"

            dest_dir_abs = naming.resolve_destination(root, relative_dir)
            pairs.append((video_path, dest_dir_abs / f"{file_stem}{video_path.suffix}"))
        return _OrganizePlan(src_dir=src_dir, video_dest_pairs=pairs)

    async def _same_content(self, src: Path, dest: Path) -> bool:
        """Idempotency check (task 2): a destination that already matches
        size (+ duration for video) is treated as already organized."""
        if not dest.exists():  # noqa: ASYNC240 -- cheap stat, not a long-running op
            return False
        src_size = _file_size(src)
        if src.suffix == ".mkv":
            src_probe = await self._ffmpeg.probe(src)
            dest_probe = await self._ffmpeg.probe(dest)
            if dest_probe is None or dest_probe.size_bytes != src_size:
                return False
            if src_probe is not None and src_probe.duration_seconds is not None:
                expected = int(src_probe.duration_seconds)
                return _duration_matches(dest_probe.duration_seconds, expected)
            return True
        return src_size > 0 and src_size == _file_size(dest)

    async def _place_file(
        self, ctx: JobContext, src: Path, dest: Path, *, role: str
    ) -> Path | None:
        """Returns the final path once `src` is in place, or `None` if
        `on_conflict: review` hit a hard conflict -- the caller parks the job
        in `needs_review` and stops (nothing else is touched)."""
        lib_cfg = ctx.config.library
        if dest.exists():  # noqa: ASYNC240 -- cheap stat, not a long-running op
            if await self._same_content(src, dest):
                return dest
            if lib_cfg.on_conflict == "review":
                return None
            if lib_cfg.on_conflict == "overwrite":
                self._quarantine(ctx, dest)
            else:  # keep_both
                dest = _next_available_name(dest)

        await self._move_file(ctx, src, dest, emit_progress=(role == "transcoded"))
        with contextlib.suppress(OSError):
            dest.chmod(lib_cfg.file_mode)
        with contextlib.suppress(OSError):
            dest.parent.chmod(lib_cfg.dir_mode)
        return dest

    def _quarantine(self, ctx: JobContext, dest: Path) -> None:
        """`overwrite`: never delete a user's file outright -- move it to
        `data_dir/replaced/` first (task 2)."""
        assert ctx.data_dir is not None  # noqa: S101 -- checked by run() before _place_file
        replaced_dir = ctx.data_dir / "replaced"
        replaced_dir.mkdir(parents=True, exist_ok=True)
        target = _next_available_name(replaced_dir / dest.name)
        os.replace(dest, target)

    async def _move_file(
        self, ctx: JobContext, src: Path, dest: Path, *, emit_progress: bool
    ) -> None:
        """Atomic-ish move (task 2): same-device is a plain rename; a
        cross-device copy lands at `<dest>.spindle-part`, gets fsync'd, and
        is only renamed to its final name (`os.replace`) once fully written
        -- a partially copied file must never appear under its final name."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _same_device(src, dest):
            await asyncio.to_thread(os.replace, src, dest)
            return

        tmp_dest = dest.with_name(dest.name + ".spindle-part")
        size = _file_size(src)
        try:
            if emit_progress and size > 0:
                await self._copy_with_progress(ctx, src, tmp_dest, total_bytes=size)
            else:
                await asyncio.to_thread(
                    _blocking_copy_with_fsync, src, tmp_dest, _ORGANIZE_COPY_CHUNK_BYTES
                )
        except Exception:
            with contextlib.suppress(OSError):
                tmp_dest.unlink(missing_ok=True)
            raise
        await asyncio.to_thread(os.replace, tmp_dest, dest)
        await asyncio.to_thread(src.unlink)

    async def _copy_with_progress(
        self, ctx: JobContext, src: Path, tmp_dest: Path, *, total_bytes: int
    ) -> None:
        """A 30 GB copy over the network is a visible part of the user's
        wait (task 2) -- polls the partial file's size from the async side
        while the blocking copy runs in a thread, rather than threading a
        callback across the loop boundary."""
        throttler = Throttler(1.0)
        stop = asyncio.Event()

        async def _poll() -> None:
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                if stop.is_set():
                    return
                if not throttler.should_emit():
                    continue
                progress = min(1.0, _file_size(tmp_dest) / total_bytes) if total_bytes else 0.0
                await ctx.emit(
                    EventIn(
                        type="task.progress",
                        job_id=ctx.job.id,
                        data={
                            "kind": TaskKind.ORGANIZE.value,
                            "progress": progress,
                            "message": f"Copying {tmp_dest.name.removesuffix('.spindle-part')}",
                        },
                    )
                )

        poll_task = asyncio.create_task(_poll())
        try:
            await asyncio.to_thread(
                _blocking_copy_with_fsync, src, tmp_dest, _ORGANIZE_COPY_CHUNK_BYTES
            )
        finally:
            stop.set()
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task

    async def _has_enough_free_space(self, ctx: JobContext, total_bytes: int) -> bool:
        assert ctx.library_dir is not None  # noqa: S101 -- checked by run() before this call
        usage = await asyncio.to_thread(shutil.disk_usage, ctx.library_dir)
        headroom = int(total_bytes * _ORGANIZE_PREFLIGHT_HEADROOM)
        required = max(headroom, ctx.config.behaviour.min_free_bytes)
        return usage.free >= required

    async def _refresh_jellyfin(self, ctx: JobContext, dest_dir: Path | None) -> None:
        """Best-effort (task 3): Jellyfin being down must never fail a
        completed rip -- it becomes exactly one `system.warning`."""
        if not self._jellyfin.is_configured:
            return

        refreshed = await self._jellyfin.refresh_library(str(dest_dir) if dest_dir else None)
        if not refreshed:
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=ctx.job.id,
                    data={
                        "code": "jellyfin_unreachable",
                        "message": "Could not reach Jellyfin to refresh the library.",
                    },
                )
            )
            return

        if ctx.job.tmdb_id is not None and await self._jellyfin.find_item(ctx.job.tmdb_id) is None:
            await ctx.emit(
                EventIn(
                    type="system.warning",
                    job_id=ctx.job.id,
                    data={
                        "code": "jellyfin_item_not_found",
                        "message": "Jellyfin did not show this item after a library refresh.",
                    },
                )
            )


class NotifyStage:
    """Sends the `job.completed` notification, per
    phase-06-library-jellyfin.md task 4.

    `NOTIFY` is the last `TaskKind` in pipeline order, so by the time this
    runs every prior task has already succeeded/skipped (`needs_review` and
    `failed` jobs drop out of the dispatcher's active-jobs query at the
    stage that set them -- they never reach `NOTIFY`). Always best-effort
    (architecture.md §1's stage-skip table: "notify | never skipped, always
    best-effort, never fails a job") -- an unconfigured/failed webhook is a
    `SKIPPED` result, never `FAILED`.
    """

    kind: ClassVar[TaskKind] = TaskKind.NOTIFY

    def __init__(self, notifier: NotificationService) -> None:
        self._notifier = notifier

    async def can_skip(self, ctx: JobContext) -> bool:
        return False

    async def run(self, ctx: JobContext) -> StageResult:
        job = ctx.job
        duration_seconds = (
            int((utc_now() - job.started_at).total_seconds()) if job.started_at else None
        )
        output_path = (job.meta.get("organize") or {}).get("destination")

        sent = await self._notifier.notify_job(
            job,
            event="completed",
            config=ctx.config.notifications,
            duration_seconds=duration_seconds,
            output_path=output_path,
        )
        return StageResult(
            status=TaskStatus.SUCCEEDED if sent else TaskStatus.SKIPPED,
            message=None if sent else "Notifications disabled or no webhook configured.",
        )

    async def cleanup(self, ctx: JobContext) -> None:
        return None
