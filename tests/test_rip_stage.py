from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.ffmpeg import MediaProbe
from spindle.adapters.handbrake import HandBrakeDiscScan, HandBrakeTitleInfo
from spindle.adapters.makemkv import DiscScan, MakeMkvError, MakeMkvTitleInfo, RipProgress
from spindle.config import BehaviourConfig, RipConfig, RuntimeConfig
from spindle.models.drive import DriveStatus
from spindle.models.job import JobStatus, MediaKind
from spindle.models.task import TaskStatus
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import RipStage
from spindle.services.events import EventIn
from spindle.utils import fsutil
from tests.factories import make_disc, make_drive, make_job


class _FakeMakeMkv:
    def __init__(self, *, titles: list[MakeMkvTitleInfo], available: bool = True) -> None:
        self.titles = titles
        self.available = available
        self.rip_calls: list[int] = []
        self.cancel_calls: list[int] = []
        self.fail_on_title: int | None = None
        self.content: dict[int, bytes] = {}

    async def is_available(self) -> bool:
        return self.available

    async def scan(self, device: str) -> DiscScan:
        return DiscScan(titles=tuple(self.titles))

    async def rip_title(
        self,
        device: str,
        title_id: int,
        out_dir: Path,
        on_progress: Any = None,
        *,
        handle: int | None = None,
    ) -> Path:
        self.rip_calls.append(title_id)
        if self.fail_on_title == title_id:
            raise MakeMkvError("simulated rip failure")
        if on_progress is not None:
            await on_progress(RipProgress(progress=1.0, message="done"))
        out_path = out_dir / f"raw_{title_id}.mkv"
        out_path.write_bytes(self.content.get(title_id, b"x" * 1024))
        return out_path

    async def cancel(self, handle: int, *, grace_period: float = 10.0) -> None:
        self.cancel_calls.append(handle)


class _FakeHandBrake:
    def __init__(self, *, titles: list[HandBrakeTitleInfo] | None = None) -> None:
        self.titles = titles or []

    async def scan_disc(self, device: str) -> HandBrakeDiscScan:
        return HandBrakeDiscScan(titles=tuple(self.titles))


class _FakeFfmpeg:
    def __init__(
        self, *, durations: dict[str, float] | None = None, always_valid: bool = False
    ) -> None:
        self.durations = durations or {}
        self.always_valid = always_valid

    async def probe(self, path: Path) -> MediaProbe | None:
        size_bytes = await asyncio.to_thread(_size_or_zero, path)
        if size_bytes == 0:
            return None
        duration = self.durations.get(path.name)
        if duration is None and not self.always_valid:
            return None
        return MediaProbe(
            duration_seconds=duration if duration is not None else 7200.0,
            size_bytes=size_bytes,
            has_video=True,
            video_codec="hevc",
            width=1920,
            height=1080,
        )


def _size_or_zero(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


class _FakeOptical:
    def __init__(self) -> None:
        self.eject_calls: list[str] = []

    def eject(self, dev: str) -> None:
        self.eject_calls.append(dev)


def _title(
    title_id: int, *, duration: int = 7200, chapters: int = 24, size: int = 30 * 1024**3
) -> MakeMkvTitleInfo:
    return MakeMkvTitleInfo(
        title_id=title_id,
        name=f"title{title_id}",
        duration_seconds=duration,
        size_bytes=size,
        chapter_count=chapters,
        output_filename=f"title_t{title_id:02d}.mkv",
        streams=(),
    )


def _config(*, min_free_bytes: int = 0, title_selection: str = "longest") -> RuntimeConfig:
    return RuntimeConfig(
        rip=RipConfig(title_selection=title_selection),  # type: ignore[arg-type]
        behaviour=BehaviourConfig(min_free_bytes=min_free_bytes),
    )


async def _make_context(
    session: AsyncSession,
    tmp_path: Path,
    *,
    config: RuntimeConfig,
    drive_status: DriveStatus = DriveStatus.READY,
    media_kind: MediaKind = MediaKind.MOVIE,
) -> tuple[JobContext, list[EventIn]]:
    drive = await make_drive(session, status=drive_status)
    disc = await make_disc(session, drive_id=drive.id)
    job = await make_job(session, disc_id=disc.id, source_path=None, media_kind=media_kind)
    drive.current_job_id = job.id
    await session.commit()

    events: list[EventIn] = []

    async def emit(event: EventIn) -> None:
        events.append(event)

    ctx = JobContext(
        job=job,
        disc=disc,
        drive=drive,
        work_dir=tmp_path,
        config=config,
        emit=emit,
        logger=structlog.get_logger(__name__),
    )
    return ctx, events


async def test_run_rips_the_selected_title_and_verifies_it(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 7200.0})
    optical = _FakeOptical()
    stage = RipStage(makemkv, _FakeHandBrake(), ffmpeg, optical)  # type: ignore[arg-type]

    ctx, events = await _make_context(session, tmp_path, config=_config())

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta["accepted"] is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0] == fsutil.raw_dir(tmp_path, ctx.job.id) / "title.mkv"
    assert makemkv.rip_calls == [0]
    assert ctx.job.meta["titles"][0]["title_id"] == 0

    # drive released and disc ejected once the rip is done
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.EMPTY
    assert ctx.drive.current_job_id is None
    assert optical.eject_calls == [ctx.drive.device_path]

    progress_events = [e for e in events if e.type == "task.progress"]
    assert progress_events
    assert progress_events[-1].data["progress"] == 1.0


async def test_can_skip_true_when_a_verified_output_already_exists(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 7200.0})
    stage = RipStage(makemkv, _FakeHandBrake(), ffmpeg, _FakeOptical())  # type: ignore[arg-type]

    ctx, _events = await _make_context(session, tmp_path, config=_config())
    raw = fsutil.raw_dir(tmp_path, ctx.job.id)
    raw.mkdir(parents=True)
    (raw / "title.mkv").write_bytes(b"already ripped")

    assert await stage.can_skip(ctx) is True
    assert makemkv.rip_calls == []


async def test_can_skip_false_when_output_is_missing(session: AsyncSession, tmp_path: Path) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 7200.0})
    stage = RipStage(makemkv, _FakeHandBrake(), ffmpeg, _FakeOptical())  # type: ignore[arg-type]

    ctx, _events = await _make_context(session, tmp_path, config=_config())

    assert await stage.can_skip(ctx) is False


async def test_run_parks_for_review_when_no_title_survives_selection(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0, duration=30)])  # far too short
    optical = _FakeOptical()
    stage = RipStage(makemkv, _FakeHandBrake(), _FakeFfmpeg(), optical)  # type: ignore[arg-type]

    ctx, _events = await _make_context(
        session, tmp_path, config=_config(), drive_status=DriveStatus.READY
    )

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta == {"accepted": False}
    assert ctx.job.status == JobStatus.NEEDS_REVIEW
    assert makemkv.rip_calls == []
    # a title too ambiguous/short to rip doesn't claim or eject the drive
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.READY
    assert optical.eject_calls == []


async def test_run_fails_when_not_enough_free_space(session: AsyncSession, tmp_path: Path) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    optical = _FakeOptical()
    config = _config(min_free_bytes=10**18)
    stage = RipStage(makemkv, _FakeHandBrake(), _FakeFfmpeg(), optical)  # type: ignore[arg-type]

    ctx, events = await _make_context(session, tmp_path, config=config)

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "space" in (result.message or "")
    assert makemkv.rip_calls == []
    assert any(e.type == "system.warning" for e in events)
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.READY
    assert optical.eject_calls == []


async def test_run_fails_and_recovers_the_drive_when_verification_fails(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    ffmpeg = _FakeFfmpeg()  # no durations configured -> probe() always returns None
    optical = _FakeOptical()
    stage = RipStage(makemkv, _FakeHandBrake(), ffmpeg, optical)  # type: ignore[arg-type]

    ctx, _events = await _make_context(session, tmp_path, config=_config())

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "verification" in (result.message or "")
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.EMPTY
    assert ctx.drive.current_job_id is None
    assert optical.eject_calls == [ctx.drive.device_path]
    raw = fsutil.raw_dir(tmp_path, ctx.job.id)
    assert not (raw / "title.mkv").exists()


async def test_run_fails_cleanly_when_makemkv_is_unavailable(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[], available=False)
    handbrake = _FakeHandBrake(
        titles=[
            HandBrakeTitleInfo(
                title_id=1,
                duration_seconds=7200,
                chapter_count=24,
                audio_tracks=(),
                subtitle_tracks=(),
            )
        ]
    )
    optical = _FakeOptical()
    stage = RipStage(makemkv, handbrake, _FakeFfmpeg(), optical)  # type: ignore[arg-type]

    ctx, _events = await _make_context(session, tmp_path, config=_config())

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "MakeMKV" in (result.message or "")
    assert ctx.job.meta["titles"]  # selection still ran against the HandBrake scan
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.READY
    assert optical.eject_calls == []


async def test_cleanup_cancels_the_rip_deletes_the_partial_file_and_recovers_the_drive(
    session: AsyncSession, tmp_path: Path
) -> None:
    makemkv = _FakeMakeMkv(titles=[_title(0)])
    optical = _FakeOptical()
    stage = RipStage(makemkv, _FakeHandBrake(), _FakeFfmpeg(), optical)  # type: ignore[arg-type]

    ctx, _events = await _make_context(session, tmp_path, config=_config())
    raw = fsutil.raw_dir(tmp_path, ctx.job.id)
    raw.mkdir(parents=True)
    partial = raw / "title.mkv"
    partial.write_bytes(b"partial data")
    stage._active_output[ctx.job.id] = partial  # simulates a rip cancelled mid-write

    await stage.cleanup(ctx)

    assert makemkv.cancel_calls == [ctx.job.id]
    assert not partial.exists()
    assert ctx.drive is not None
    assert ctx.drive.status == DriveStatus.EMPTY
    assert ctx.drive.current_job_id is None
    assert optical.eject_calls == [ctx.drive.device_path]
