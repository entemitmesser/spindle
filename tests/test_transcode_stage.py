from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.ffmpeg import MediaProbe
from spindle.adapters.handbrake import EncodeProgress, HandBrakeError
from spindle.config import BehaviourConfig, RipConfig, RuntimeConfig, TranscodeConfig
from spindle.models.disc import DiscType
from spindle.models.job import JobStatus, MediaKind
from spindle.models.task import TaskStatus
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import TranscodeStage, _progress_message
from spindle.services.events import EventIn
from spindle.utils import fsutil
from tests.factories import make_disc, make_drive, make_job


class _FakeHandBrake:
    def __init__(self, *, encoders: tuple[str, ...] = ("x265",)) -> None:
        self.encoders = encoders
        self.encode_calls: list[Path] = []
        self.cancel_calls: list[int] = []
        self.fail_on: str | None = None
        self.content: dict[str, bytes] = {}

    async def available_encoders(self) -> tuple[str, ...]:
        return self.encoders

    async def encode(
        self,
        src: Path,
        dst: Path,
        *,
        preset_file: Path,
        preset_name: str,
        overrides: Any = (),
        on_progress: Any = None,
        handle: int | None = None,
    ) -> None:
        self.encode_calls.append(src)
        if self.fail_on == src.name:
            raise HandBrakeError("simulated encode failure")
        if on_progress is not None:
            await on_progress(
                EncodeProgress(progress=1.0, eta_seconds=0, fps=42.0, pass_id=0, pass_count=1)
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(self.content.get(src.name, b"x" * 1024))  # noqa: ASYNC240 -- test fake

    async def cancel(self, handle: int, *, grace_period: float = 10.0) -> None:
        self.cancel_calls.append(handle)


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
            duration_seconds=duration if duration is not None else 300.0,
            size_bytes=size_bytes,
            has_video=True,
            video_codec="hevc",
            width=1920,
            height=1080,
        )


def _size_or_zero(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _config(*, min_free_bytes: int = 0, **transcode_overrides: object) -> RuntimeConfig:
    return RuntimeConfig(
        behaviour=BehaviourConfig(min_free_bytes=min_free_bytes),
        transcode=TranscodeConfig(**transcode_overrides),  # type: ignore[arg-type]
    )


async def _make_context(
    session: AsyncSession,
    tmp_path: Path,
    *,
    config: RuntimeConfig,
    disc_type: DiscType = DiscType.BLURAY,
    media_kind: MediaKind = MediaKind.MOVIE,
) -> tuple[JobContext, list[EventIn]]:
    drive = await make_drive(session)
    disc = await make_disc(session, drive_id=drive.id, disc_type=disc_type)
    job = await make_job(session, disc_id=disc.id, source_path=None, media_kind=media_kind)
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


def _write_source(
    tmp_path: Path, job_id: int, name: str, *, content: bytes = b"raw source bytes " * 200
) -> Path:
    raw = fsutil.raw_dir(tmp_path, job_id)
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    path.write_bytes(content)
    return path


async def test_run_transcodes_a_five_minute_source_and_verifies_the_output(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")

    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta["accepted"] is True
    dest = fsutil.transcode_dir(tmp_path, ctx.job.id) / "title.mkv"
    assert result.artifacts == [dest]
    assert dest.exists()
    assert not fsutil.raw_dir(tmp_path, ctx.job.id).exists()
    assert ctx.job.meta["encoder"] == "x265"

    progress_events = [e for e in events if e.type == "task.progress"]
    assert progress_events
    assert progress_events[-1].data["progress"] == 1.0
    assert "x265" in progress_events[-1].data["message"]


async def test_run_keeps_the_raw_dir_when_configured(session: AsyncSession, tmp_path: Path) -> None:
    config = RuntimeConfig(
        rip=RipConfig(keep_raw_after_transcode=True),  # type: ignore[arg-type]
        transcode=TranscodeConfig(),
    )
    ctx, _events = await _make_context(session, tmp_path, config=config)
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    await stage.run(ctx)

    assert fsutil.raw_dir(tmp_path, ctx.job.id).exists()


async def test_available_encoders_reporting_no_hardware_falls_back_to_x265_without_failing(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(hardware_encoder="auto"))
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake(encoders=("x264", "x265"))  # no hardware encoder passed its probe
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert ctx.job.meta["encoder"] == "x265"


async def test_can_skip_true_when_a_verified_output_already_exists(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    out_dir = fsutil.transcode_dir(tmp_path, ctx.job.id)
    out_dir.mkdir(parents=True)
    (out_dir / "title.mkv").write_bytes(b"already transcoded" * 100)

    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    assert await stage.can_skip(ctx) is True
    assert handbrake.encode_calls == []


async def test_can_skip_false_when_output_is_missing(session: AsyncSession, tmp_path: Path) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    assert await stage.can_skip(ctx) is False


async def test_can_skip_false_when_transcode_disabled(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(enabled=False))
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    assert await stage.can_skip(ctx) is False


async def test_run_promotes_the_source_files_when_transcode_is_disabled(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(enabled=False))
    source = _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(always_valid=True)
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.artifacts == [source]
    assert handbrake.encode_calls == []
    assert ctx.job.meta["encoder"] == "passthrough"
    # nothing to transcode -> raw must survive regardless of keep_raw_after_transcode
    assert source.exists()


async def test_run_never_uses_hardware_for_the_dvd_remux_preset(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(
        session, tmp_path, config=_config(hardware_encoder="auto"), disc_type=DiscType.DVD
    )
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake(encoders=("x265", "nvenc_h265"))
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert ctx.job.meta["encoder"] == "x265"


async def test_run_parks_for_review_when_verification_fails(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    # No duration configured for the output -> probe() returns None -> verification fails.
    ffmpeg = _FakeFfmpeg()
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta == {"accepted": False}
    assert ctx.job.status == JobStatus.NEEDS_REVIEW
    assert "looks wrong" in (result.message or "")
    dest = fsutil.transcode_dir(tmp_path, ctx.job.id) / "title.mkv"
    assert not dest.exists()


async def test_run_fails_when_not_enough_free_space(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    # The 10 GB floor (phase-04 task 4) applies regardless of source size, so
    # faking the volume down to nothing is the simplest way to trip it.
    monkeypatch.setattr(
        "spindle.pipeline.stages.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=0, used=0, free=0),
    )

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "space" in (result.message or "")
    assert handbrake.encode_calls == []
    assert any(e.type == "system.warning" for e in events)


async def test_run_fails_when_handbrake_raises(session: AsyncSession, tmp_path: Path) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    _write_source(tmp_path, ctx.job.id, "title.mkv")
    handbrake = _FakeHandBrake()
    handbrake.fail_on = "title.mkv"
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "Transcode failed" in (result.message or "")


async def test_cleanup_cancels_the_encode_and_deletes_the_partial_file(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    out_dir = fsutil.transcode_dir(tmp_path, ctx.job.id)
    out_dir.mkdir(parents=True)
    partial = out_dir / "title.mkv"
    partial.write_bytes(b"partial data")
    stage._active_output[ctx.job.id] = partial

    await stage.cleanup(ctx)

    assert handbrake.cancel_calls == [ctx.job.id]
    assert not partial.exists()


async def test_run_transcodes_multiple_titles_sequentially(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(
        session, tmp_path, config=_config(), media_kind=MediaKind.SERIES
    )
    _write_source(tmp_path, ctx.job.id, "title-01.mkv", content=b"a" * 100)
    _write_source(tmp_path, ctx.job.id, "title-02.mkv", content=b"b" * 100)
    handbrake = _FakeHandBrake()
    ffmpeg = _FakeFfmpeg(durations={"title-01.mkv": 300.0, "title-02.mkv": 300.0})
    stage = TranscodeStage(handbrake, ffmpeg)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert len(result.artifacts) == 2
    out_dir = fsutil.transcode_dir(tmp_path, ctx.job.id)
    assert (out_dir / "title-01.mkv").exists()
    assert (out_dir / "title-02.mkv").exists()
    assert handbrake.encode_calls == [
        fsutil.raw_dir(tmp_path, ctx.job.id) / "title-01.mkv",
        fsutil.raw_dir(tmp_path, ctx.job.id) / "title-02.mkv",
    ]


def test_progress_message_includes_fps_and_file_index_for_multi_title_jobs() -> None:
    progress = EncodeProgress(progress=0.5, eta_seconds=60, fps=48.0, pass_id=0, pass_count=1)

    assert _progress_message("x265", progress, 2, 6) == "x265 · 48 fps · file 2 of 6"


def test_progress_message_omits_file_index_for_a_single_title() -> None:
    progress = EncodeProgress(progress=0.5, eta_seconds=60, fps=None, pass_id=0, pass_count=1)

    assert _progress_message("x265", progress, 1, 1) == "x265"
