from __future__ import annotations

from pathlib import Path
from typing import Any

from spindle.adapters.mkvpropedit import TrackEdit
from spindle.config import LibraryConfig
from spindle.models.job import MediaKind
from spindle.services.metadata import MetadataService
from tests.factories import make_job


class _FakeMkvPropEdit:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.apply_calls: list[dict[str, Any]] = []

    async def is_available(self) -> bool:
        return self.available

    async def apply(self, path: Path, *, title: str | None, tracks: list[TrackEdit]) -> None:
        self.apply_calls.append({"path": path, "title": title, "tracks": tracks})


class _FakeTmdb:
    def __init__(self, details: dict[str, Any] | None = None) -> None:
        self._details = details or {}
        self.detail_calls: list[tuple[int, str]] = []
        self.downloaded: list[tuple[str, Path]] = []

    async def details(self, tmdb_id: int, *, media_kind: str = "movie") -> dict[str, Any] | None:
        self.detail_calls.append((tmdb_id, media_kind))
        return self._details

    async def download_image(self, image_path: str, dest: Path, *, size: str = "original") -> bool:
        self.downloaded.append((image_path, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-image")  # noqa: ASYNC240 -- test fake
        return True


async def test_tag_sets_title_and_returns_true_when_mkvpropedit_available(session, tmp_path):
    job = await make_job(session, title="Amelie", year=2001)
    mkvpropedit = _FakeMkvPropEdit()
    service = MetadataService(mkvpropedit, _FakeTmdb())  # type: ignore[arg-type]
    path = tmp_path / "movie.mkv"

    ok = await service.tag(path, job=job, tracks=[])

    assert ok is True
    assert mkvpropedit.apply_calls == [{"path": path, "title": "Amelie (2001)", "tracks": []}]


async def test_tag_returns_false_when_mkvpropedit_unavailable(session, tmp_path):
    job = await make_job(session, title="Amelie", year=2001)
    mkvpropedit = _FakeMkvPropEdit(available=False)
    service = MetadataService(mkvpropedit, _FakeTmdb())  # type: ignore[arg-type]

    ok = await service.tag(tmp_path / "movie.mkv", job=job, tracks=[])

    assert ok is False
    assert mkvpropedit.apply_calls == []


async def test_fetch_artwork_downloads_poster_and_backdrop(session, tmp_path):
    job = await make_job(session, tmdb_id=27205)
    tmdb = _FakeTmdb({"poster_path": "/poster.jpg", "backdrop_path": "/fanart.jpg"})
    service = MetadataService(_FakeMkvPropEdit(), tmdb)  # type: ignore[arg-type]

    saved = await service.fetch_artwork(job, tmp_path)

    assert {p.name for p in saved} == {"poster.jpg", "fanart.jpg"}
    for path in saved:
        assert path.read_bytes() == b"fake-image"


async def test_fetch_artwork_returns_empty_without_tmdb_id(session, tmp_path):
    job = await make_job(session, tmdb_id=None)
    tmdb = _FakeTmdb({"poster_path": "/poster.jpg"})
    service = MetadataService(_FakeMkvPropEdit(), tmdb)  # type: ignore[arg-type]

    saved = await service.fetch_artwork(job, tmp_path)

    assert saved == []
    assert tmdb.detail_calls == []


async def test_write_nfo_returns_none_when_disabled(session, tmp_path):
    job = await make_job(session, title="Amelie", year=2001, tmdb_id=27205)
    service = MetadataService(_FakeMkvPropEdit(), _FakeTmdb())  # type: ignore[arg-type]

    result = await service.write_nfo(job, tmp_path / "movie.nfo", config=LibraryConfig())

    assert result is None
    assert not (tmp_path / "movie.nfo").exists()


async def test_write_nfo_writes_title_year_and_plot_when_enabled(session, tmp_path):
    job = await make_job(session, title="Amelie", year=2001, tmdb_id=27205, imdb_id="tt0211915")
    tmdb = _FakeTmdb({"overview": "A whimsical waitress in Paris."})
    service = MetadataService(_FakeMkvPropEdit(), tmdb)  # type: ignore[arg-type]
    dest = tmp_path / "movie.nfo"

    config = LibraryConfig(write_nfo=True)  # type: ignore[call-arg]
    result = await service.write_nfo(job, dest, config=config)

    assert result == dest
    content = dest.read_text(encoding="utf-8")
    assert "<title>Amelie</title>" in content
    assert "<year>2001</year>" in content
    assert "<tmdbid>27205</tmdbid>" in content
    assert "<imdbid>tt0211915</imdbid>" in content
    assert "whimsical waitress" in content


async def test_write_nfo_uses_episodedetails_root_for_series(session, tmp_path):
    job = await make_job(session, title="Show", year=2020, media_kind=MediaKind.SERIES)
    service = MetadataService(_FakeMkvPropEdit(), _FakeTmdb())  # type: ignore[arg-type]
    dest = tmp_path / "ep.nfo"

    config = LibraryConfig(write_nfo=True)  # type: ignore[call-arg]
    await service.write_nfo(job, dest, config=config)

    content = dest.read_text(encoding="utf-8")
    assert content.strip().startswith("<?xml")
    assert "<episodedetails>" in content
    assert "</episodedetails>" in content
