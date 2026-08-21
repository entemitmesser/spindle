from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.ffmpeg import MediaProbe
from spindle.config import LibraryConfig, RuntimeConfig
from spindle.models.disc import DiscType
from spindle.models.job import JobStatus, MediaKind
from spindle.models.task import TaskStatus
from spindle.pipeline import stages as stages_module
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import OrganizeStage
from spindle.services.events import EventIn
from spindle.utils import fsutil
from tests.factories import make_disc, make_drive, make_job


class _FakeFfmpeg:
    def __init__(
        self,
        *,
        duration: float = 300.0,
        width: int = 1920,
        height: int = 1080,
        video_codec: str = "hevc",
    ) -> None:
        self.duration = duration
        self.width = width
        self.height = height
        self.video_codec = video_codec

    async def probe(self, path: Path) -> MediaProbe | None:
        if not path.exists():  # noqa: ASYNC240 -- test fake
            return None
        return MediaProbe(
            duration_seconds=self.duration,
            size_bytes=path.stat().st_size,  # noqa: ASYNC240 -- test fake
            has_video=True,
            video_codec=self.video_codec,
            width=self.width,
            height=self.height,
            audio_codec="ac3",
        )


class _FakeJellyfin:
    def __init__(
        self, *, configured: bool = True, refresh_ok: bool = True, found: bool = True
    ) -> None:
        self.is_configured = configured
        self._refresh_ok = refresh_ok
        self._found = found
        self.refresh_calls: list[str | None] = []
        self.find_calls: list[int] = []

    async def refresh_library(self, path_hint: str | None = None) -> bool:
        self.refresh_calls.append(path_hint)
        return self._refresh_ok

    async def find_item(self, tmdb_id: int) -> dict[str, Any] | None:
        self.find_calls.append(tmdb_id)
        return {"Id": "1"} if self._found else None


def _config(**library_overrides: object) -> RuntimeConfig:
    return RuntimeConfig(library=LibraryConfig(**library_overrides))  # type: ignore[arg-type]


async def _make_context(
    session: AsyncSession,
    tmp_path: Path,
    *,
    config: RuntimeConfig | None = None,
    title: str = "Amelie",
    year: int | None = 2001,
    tmdb_id: int | None = 27205,
    media_kind: MediaKind = MediaKind.MOVIE,
    season: int | None = None,
) -> tuple[JobContext, list[EventIn]]:
    drive = await make_drive(session)
    disc = await make_disc(session, drive_id=drive.id, disc_type=DiscType.BLURAY)
    job = await make_job(
        session,
        disc_id=disc.id,
        source_path=None,
        media_kind=media_kind,
        title=title,
        year=year,
        tmdb_id=tmdb_id,
        season=season,
    )
    await session.commit()

    # library_dir/data_dir are real mounted volumes in production; tests need
    # them to exist upfront so shutil.disk_usage() in the preflight check has
    # something to stat.
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    events: list[EventIn] = []

    async def emit(event: EventIn) -> None:
        events.append(event)

    ctx = JobContext(
        job=job,
        disc=disc,
        drive=drive,
        work_dir=tmp_path / "work",
        config=config or _config(),
        emit=emit,
        logger=structlog.get_logger(__name__),
        library_dir=tmp_path / "library",
        data_dir=tmp_path / "data",
    )
    return ctx, events


def _write_transcoded(
    work_dir: Path, job_id: int, name: str = "title.mkv", content: bytes = b"video bytes " * 100
) -> Path:
    out_dir = fsutil.transcode_dir(work_dir, job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(content)
    return path


def _stage(*, ffmpeg: Any = None, jellyfin: Any = None) -> OrganizeStage:
    return OrganizeStage(ffmpeg or _FakeFfmpeg(), jellyfin or _FakeJellyfin())  # type: ignore[arg-type]


# -- basic placement -----------------------------------------------------


async def test_run_moves_the_transcoded_file_with_the_rendered_name(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    src = _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    dest = tmp_path / "library" / "Movies" / "Amelie (2001)" / "Amelie (2001) - Bluray-1080p.mkv"
    assert dest.exists()
    assert dest.read_bytes() == b"video bytes " * 100
    assert not src.exists()
    # the job's work dir is cleared once everything is safely in the library
    assert not fsutil.transcode_dir(tmp_path / "work", ctx.job.id).exists()


async def test_run_moves_sidecar_subtitles_artwork_and_nfo(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    src_dir = fsutil.transcode_dir(tmp_path / "work", ctx.job.id)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    (src_dir / "title.eng.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    (src_dir / "title.deu.sdh.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
    (src_dir / "poster.jpg").write_bytes(b"poster")
    (src_dir / "fanart.jpg").write_bytes(b"fanart")
    (src_dir / "title.nfo").write_text("<movie></movie>")
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    assert (dest_dir / "Amelie (2001) - Bluray-1080p.eng.srt").read_text() == (
        "1\n00:00:01,000 --> 00:00:02,000\nHi\n"
    )
    assert (dest_dir / "Amelie (2001) - Bluray-1080p.deu.sdh.srt").exists()
    assert (dest_dir / "poster.jpg").read_bytes() == b"poster"
    assert (dest_dir / "fanart.jpg").read_bytes() == b"fanart"
    assert (dest_dir / "Amelie (2001) - Bluray-1080p.nfo").exists()


async def test_can_skip_true_when_the_destination_already_matches(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()
    await stage.run(ctx)

    # a fresh source file lands back in the (now-emptied) work dir, as it
    # would on a genuine re-run of the same job
    _write_transcoded(tmp_path / "work", ctx.job.id)

    assert await stage.can_skip(ctx) is True


async def test_run_fails_without_a_job_id() -> None:
    from spindle.models.job import Job

    stage = _stage()
    ctx = JobContext(
        job=Job(title="X"),
        disc=None,
        drive=None,
        work_dir=Path("."),
        config=_config(),
        emit=lambda e: None,  # type: ignore[arg-type, return-value]
        logger=structlog.get_logger(__name__),
        library_dir=Path("."),
        data_dir=Path("."),
    )
    result = await stage.run(ctx)
    assert result.status == TaskStatus.FAILED


async def test_run_fails_when_library_dir_is_not_configured(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    ctx.library_dir = None
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED


async def test_run_fails_when_there_is_nothing_to_organize(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert result.message == "No transcoded files to organize."


# -- template edge cases (umlauts, colons, very long titles) ----------------


async def test_run_handles_umlauts_and_colons_in_the_title(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, title="Mission: Impossible — Fünf")
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    dest_dir = tmp_path / "library" / "Movies"
    children = list(dest_dir.iterdir())
    assert len(children) == 1
    assert ":" not in children[0].name
    assert "Fünf" in children[0].name


async def test_run_handles_a_long_title(session: AsyncSession, tmp_path: Path) -> None:
    # The exact 180-char/word-boundary truncation contract is covered without
    # touching a real filesystem in test_naming.py::
    # test_sanitize_segment_truncates_a_very_long_title_on_a_word_boundary --
    # a *fully* truncated dir segment (~180 chars) plus a *fully* truncated
    # file segment (~180 chars) in the same path exceeds Windows' 260-char
    # MAX_PATH on its own, with zero prefix, so it can't be exercised through
    # a real write on this dev platform. This is a long-but-under-the-cap
    # title, just to confirm OrganizeStage's own plumbing doesn't choke on one.
    long_title = "A Rather Long But Not Extreme Movie Title For Testing"
    ctx, _events = await _make_context(session, tmp_path, title=long_title)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    dest_dir = tmp_path / "library" / "Movies"
    children = list(dest_dir.iterdir())
    assert len(children) == 1
    assert children[0].stem.startswith(long_title)


async def test_run_fails_clearly_for_an_unknown_template_variable(
    session: AsyncSession, tmp_path: Path
) -> None:
    # A template this broken is normally rejected at settings-save time
    # (see test_naming.py's validate_template coverage) -- `model_construct`
    # bypasses that validator so this test can confirm OrganizeStage itself
    # also fails cleanly rather than crashing, as defense in depth.
    bad_library = LibraryConfig.model_construct(
        **{**LibraryConfig().model_dump(), "movie_file_template": "{title} {nonexistent}"}
    )
    ctx, _events = await _make_context(session, tmp_path)
    ctx.config = RuntimeConfig.model_construct(
        **{**RuntimeConfig().model_dump(), "library": bad_library}
    )
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert "template" in (result.message or "").lower()


# -- conflict handling -----------------------------------------------------


async def test_run_review_conflict_parks_needs_review_and_touches_nothing(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(on_conflict="review"))
    src = _write_transcoded(tmp_path / "work", ctx.job.id, content=b"new content " * 50)
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    dest_dir.mkdir(parents=True)
    existing = dest_dir / "Amelie (2001) - Bluray-1080p.mkv"
    existing.write_bytes(b"old content " * 999)

    stage = _stage()
    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta.get("accepted") is False
    assert ctx.job.status == JobStatus.NEEDS_REVIEW
    assert existing.read_bytes() == b"old content " * 999
    assert src.exists()


async def test_run_overwrite_conflict_quarantines_the_existing_file(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(on_conflict="overwrite"))
    _write_transcoded(tmp_path / "work", ctx.job.id, content=b"new content " * 50)
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "Amelie (2001) - Bluray-1080p.mkv"
    dest.write_bytes(b"old content " * 999)

    stage = _stage()
    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert dest.read_bytes() == b"new content " * 50
    replaced = list((tmp_path / "data" / "replaced").iterdir())
    assert len(replaced) == 1
    assert replaced[0].read_bytes() == b"old content " * 999


async def test_run_keep_both_conflict_appends_a_numeric_suffix(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(on_conflict="keep_both"))
    _write_transcoded(tmp_path / "work", ctx.job.id, content=b"new content " * 50)
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    dest_dir.mkdir(parents=True)
    existing = dest_dir / "Amelie (2001) - Bluray-1080p.mkv"
    existing.write_bytes(b"old content " * 999)

    stage = _stage()
    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert existing.read_bytes() == b"old content " * 999
    kept = dest_dir / "Amelie (2001) - Bluray-1080p - 2.mkv"
    assert kept.exists()
    assert kept.read_bytes() == b"new content " * 50


async def test_run_is_idempotent_when_the_destination_already_matches(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()
    first = await stage.run(ctx)
    assert first.status == TaskStatus.SUCCEEDED

    _write_transcoded(tmp_path / "work", ctx.job.id)
    second = await stage.run(ctx)

    assert second.status == TaskStatus.SUCCEEDED
    assert second.meta.get("accepted") is True
    assert ctx.job.status != JobStatus.NEEDS_REVIEW


# -- cross-device move -------------------------------------------------------


async def test_cross_device_move_leaves_no_spindle_part_residue_on_success(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stages_module, "_same_device", lambda src, dest: False)
    ctx, _events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    names = [p.name for p in dest_dir.iterdir()]
    assert not any(name.endswith(".spindle-part") for name in names)
    assert "Amelie (2001) - Bluray-1080p.mkv" in names


async def test_cross_device_move_failure_leaves_source_intact_and_cleans_up_partial(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stages_module, "_same_device", lambda src, dest: False)

    def _boom(src: Path, dest: Path, chunk_size: int) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"partial")
        raise OSError("simulated disk failure mid-copy")

    monkeypatch.setattr(stages_module, "_blocking_copy_with_fsync", _boom)

    ctx, _events = await _make_context(session, tmp_path)
    src = _write_transcoded(tmp_path / "work", ctx.job.id, content=b"original bytes " * 50)
    stage = _stage()

    result = await stage.run(ctx)

    assert result.status == TaskStatus.FAILED
    assert src.exists()
    assert src.read_bytes() == b"original bytes " * 50
    dest_dir = tmp_path / "library" / "Movies" / "Amelie (2001)"
    if dest_dir.exists():
        assert not any(p.name.endswith(".spindle-part") for p in dest_dir.iterdir())
        assert not (dest_dir / "Amelie (2001) - Bluray-1080p.mkv").exists()


# -- Jellyfin (best-effort) --------------------------------------------------


async def test_jellyfin_down_still_completes_the_job_with_one_system_warning(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    jellyfin = _FakeJellyfin(refresh_ok=False)
    stage = _stage(jellyfin=jellyfin)

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    warnings = [e for e in events if e.type == "system.warning"]
    assert len(warnings) == 1
    assert warnings[0].data["code"] == "jellyfin_unreachable"
    assert jellyfin.find_calls == []  # never gets that far


async def test_jellyfin_item_not_found_after_refresh_emits_one_warning(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage(jellyfin=_FakeJellyfin(refresh_ok=True, found=False))

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    warnings = [e for e in events if e.type == "system.warning"]
    assert len(warnings) == 1
    assert warnings[0].data["code"] == "jellyfin_item_not_found"


async def test_jellyfin_not_configured_is_silently_skipped(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, events = await _make_context(session, tmp_path)
    _write_transcoded(tmp_path / "work", ctx.job.id)
    stage = _stage(jellyfin=_FakeJellyfin(configured=False))

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert not any(e.type == "system.warning" for e in events)
