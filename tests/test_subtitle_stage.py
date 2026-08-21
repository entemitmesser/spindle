from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.ffmpeg import MediaProbe, SubtitleStreamInfo
from spindle.adapters.mkvpropedit import TrackEdit
from spindle.adapters.opensubtitles import OpenSubtitlesQuotaExceededError
from spindle.config import RuntimeConfig, SubtitleConfig
from spindle.models.job import MediaKind
from spindle.models.task import TaskStatus
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import SubtitleStage
from spindle.services.events import EventIn
from spindle.utils import fsutil
from tests.factories import make_disc, make_drive, make_job

_VALID_SRT = "\n\n".join(
    f"{i}\n00:{i:02d}:00,000 --> 00:{i:02d}:02,000\nLine {i}" for i in range(1, 12)
)
_SPAM_SRT = "1\n00:00:01,000 --> 00:00:02,000\nDownload our app!\n"


class _FakeFfmpeg:
    def __init__(self, subtitle_streams: tuple[SubtitleStreamInfo, ...] = ()) -> None:
        self._subtitle_streams = subtitle_streams

    async def probe(self, path: Path) -> MediaProbe | None:
        if not path.exists():  # noqa: ASYNC240 -- test fake
            return None
        return MediaProbe(
            duration_seconds=300.0,
            size_bytes=path.stat().st_size,  # noqa: ASYNC240 -- test fake
            has_video=True,
            video_codec="hevc",
            width=1920,
            height=1080,
            subtitle_streams=self._subtitle_streams,
        )


class _FakeOpenSubtitles:
    def __init__(
        self,
        *,
        configured: bool = True,
        search_results: list[dict[str, Any]] | None = None,
        search_error: Exception | None = None,
        download_bytes: bytes | None = None,
        download_error: Exception | None = None,
    ) -> None:
        self.is_configured = configured
        self._search_results = search_results if search_results is not None else []
        self._search_error = search_error
        self._download_bytes = download_bytes if download_bytes is not None else _VALID_SRT.encode()
        self._download_error = download_error
        self.search_calls: list[dict[str, Any]] = []
        self.download_calls: list[int] = []

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        if self._search_error is not None:
            raise self._search_error
        return self._search_results

    async def download(self, file_id: int) -> bytes:
        self.download_calls.append(file_id)
        if self._download_error is not None:
            raise self._download_error
        return self._download_bytes


class _FakeMetadata:
    def __init__(self) -> None:
        self.tag_calls: list[tuple[Path, list[TrackEdit]]] = []
        self.artwork_calls = 0
        self.nfo_calls = 0

    async def tag(self, path: Path, *, job: Any, tracks: list[TrackEdit]) -> bool:
        self.tag_calls.append((path, tracks))
        return True

    async def fetch_artwork(self, job: Any, dest_dir: Path) -> list[Path]:
        self.artwork_calls += 1
        return []

    async def write_nfo(self, job: Any, dest: Path, *, config: Any) -> Path | None:
        self.nfo_calls += 1
        return None


def _candidate(
    file_id: int = 111,
    *,
    moviehash_match: bool = True,
    download_count: int = 10,
    hearing_impaired: bool = False,
    ai_translated: bool = False,
) -> dict[str, Any]:
    return {
        "attributes": {
            "release": "Movie.2020.1080p",
            "download_count": download_count,
            "moviehash_match": moviehash_match,
            "hearing_impaired": hearing_impaired,
            "ai_translated": ai_translated,
            "machine_translated": False,
            "files": [{"file_id": file_id}],
        }
    }


def _config(**subtitle_overrides: object) -> RuntimeConfig:
    return RuntimeConfig(subtitles=SubtitleConfig(**subtitle_overrides))  # type: ignore[arg-type]


async def _make_context(
    session: AsyncSession, tmp_path: Path, *, config: RuntimeConfig, tmdb_id: int | None = 27205
) -> tuple[JobContext, list[EventIn]]:
    drive = await make_drive(session)
    disc = await make_disc(session, drive_id=drive.id)
    job = await make_job(
        session,
        disc_id=disc.id,
        source_path=None,
        media_kind=MediaKind.MOVIE,
        title="Amelie",
        year=2001,
        tmdb_id=tmdb_id,
    )
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


def _write_transcoded(tmp_path: Path, job_id: int, name: str = "title.mkv") -> Path:
    out_dir = fsutil.transcode_dir(tmp_path, job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(b"transcoded bytes " * 100)
    return path


async def test_run_fails_without_a_job_id() -> None:
    stage = SubtitleStage(_FakeOpenSubtitles(), _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]
    from spindle.models.job import Job

    ctx = JobContext(
        job=Job(title="X"),
        disc=None,
        drive=None,
        work_dir=Path("."),
        config=_config(),
        emit=lambda e: None,  # type: ignore[arg-type, return-value]
        logger=structlog.get_logger(__name__),
    )
    result = await stage.run(ctx)
    assert result.status == TaskStatus.FAILED


async def test_can_skip_true_when_subtitles_disabled(session: AsyncSession, tmp_path: Path) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(enabled=False))
    stage = SubtitleStage(_FakeOpenSubtitles(), _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    assert await stage.can_skip(ctx) is True


async def test_run_skips_cleanly_when_subtitles_disabled(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(enabled=False))
    _write_transcoded(tmp_path, ctx.job.id)
    stage = SubtitleStage(_FakeOpenSubtitles(), _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert result.message == "Subtitles disabled in config."


async def test_run_skips_cleanly_with_no_provider_configured_and_job_completes(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config())
    _write_transcoded(tmp_path, ctx.job.id)
    metadata = _FakeMetadata()
    stage = SubtitleStage(
        _FakeOpenSubtitles(configured=False),
        _FakeFfmpeg(),
        metadata,  # type: ignore[arg-type]
    )

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert result.message == "No subtitle provider configured"
    # Tagging still runs even without a subtitle provider (task 4 is
    # independent of task 2/3).
    assert metadata.tag_calls


async def test_run_skips_when_external_provider_set_to_none(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(external_provider="none"))
    _write_transcoded(tmp_path, ctx.job.id)
    stage = SubtitleStage(_FakeOpenSubtitles(), _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert result.message == "No subtitle provider configured"


async def test_can_skip_true_when_all_wanted_languages_already_embedded(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(langs=["deu"]))
    embedded = (
        SubtitleStreamInfo(index=0, codec="pgs", language="deu", is_forced=False, is_default=True),
    )
    _write_transcoded(tmp_path, ctx.job.id)
    stage = SubtitleStage(_FakeOpenSubtitles(), _FakeFfmpeg(embedded), _FakeMetadata())  # type: ignore[arg-type]

    assert await stage.can_skip(ctx) is True


async def test_run_hash_hit_downloads_and_writes_sidecar_srt(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(langs=["eng"]))
    src = _write_transcoded(tmp_path, ctx.job.id)
    opensubtitles = _FakeOpenSubtitles(search_results=[_candidate()])
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert opensubtitles.search_calls[0]["moviehash"] is not None
    dest = src.with_name(f"{src.stem}.eng.srt")
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == _VALID_SRT
    subtitle_entries = [m for m in result.meta["media_files"] if m["role"] == "subtitle"]
    assert subtitle_entries[0]["language"] == "eng"
    assert subtitle_entries[0]["is_forced"] is False


async def test_run_falls_back_to_query_when_no_tmdb_id(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(
        session, tmp_path, config=_config(langs=["eng"]), tmdb_id=None
    )
    _write_transcoded(tmp_path, ctx.job.id)
    opensubtitles = _FakeOpenSubtitles(search_results=[_candidate()])
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    await stage.run(ctx)

    assert opensubtitles.search_calls[0]["tmdb_id"] is None
    assert opensubtitles.search_calls[0]["query"] == "Amelie"


async def test_run_skips_language_and_warns_on_quota_exhaustion(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, events = await _make_context(session, tmp_path, config=_config(langs=["eng"]))
    _write_transcoded(tmp_path, ctx.job.id)
    opensubtitles = _FakeOpenSubtitles(
        search_error=OpenSubtitlesQuotaExceededError("quota exceeded")
    )
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert any(e.type == "system.warning" for e in events)


async def test_run_skips_when_zero_results(session: AsyncSession, tmp_path: Path) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(langs=["eng"]))
    _write_transcoded(tmp_path, ctx.job.id)
    opensubtitles = _FakeOpenSubtitles(search_results=[])
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert result.message == "No subtitles found for the wanted languages."


async def test_run_skips_a_spam_result_and_does_not_write_it(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(langs=["eng"]))
    src = _write_transcoded(tmp_path, ctx.job.id)
    opensubtitles = _FakeOpenSubtitles(
        search_results=[_candidate()], download_bytes=_SPAM_SRT.encode()
    )
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(), _FakeMetadata())  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
    assert not src.with_name(f"{src.stem}.eng.srt").exists()


async def test_run_tags_the_container_before_fetching_subtitles(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx, _events = await _make_context(session, tmp_path, config=_config(langs=["eng"]))
    forced = (
        SubtitleStreamInfo(index=0, codec="pgs", language="deu", is_forced=True, is_default=False),
    )
    _write_transcoded(tmp_path, ctx.job.id)
    metadata = _FakeMetadata()
    opensubtitles = _FakeOpenSubtitles(search_results=[_candidate()])
    stage = SubtitleStage(opensubtitles, _FakeFfmpeg(forced), metadata)  # type: ignore[arg-type]

    await stage.run(ctx)

    assert len(metadata.tag_calls) == 1
    _path, tracks = metadata.tag_calls[0]
    assert tracks[0].language == "ger"  # mkvpropedit tagging uses 639-2/B
    assert tracks[0].is_forced is True
