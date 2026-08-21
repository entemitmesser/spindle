"""Tagging, artwork and NFO writing, per phase-05-metadata-subtitles.md task 4.

There's no dedicated `TaskKind` for "tag" — `TaskKind` is fixed (CLAUDE.md
§4) and phase 5 only adds `SUBTITLES` — so `SubtitleStage`
(`spindle/pipeline/stages.py`) calls this once transcode + subtitle fetch
have produced their tracks. Everything here is best-effort: a tagging or
artwork failure never fails the job, matching `SubtitleStage`'s own
skip-not-fail policy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from xml.sax.saxutils import escape

import structlog

from spindle.adapters.mkvpropedit import MkvPropEditAdapter, TrackEdit
from spindle.adapters.tmdb import TmdbAdapter
from spindle.config import LibraryConfig
from spindle.models.job import Job, MediaKind

logger = structlog.get_logger(__name__)


def _write_nfo_file(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


class MetadataService:
    def __init__(self, mkvpropedit: MkvPropEditAdapter, tmdb: TmdbAdapter) -> None:
        self._mkvpropedit = mkvpropedit
        self._tmdb = tmdb

    async def tag(self, path: Path, *, job: Job, tracks: list[TrackEdit]) -> bool:
        """`False` means mkvpropedit isn't installed — the caller logs and
        moves on, per CLAUDE.md §9's "absent backend, UI says so" pattern."""
        if not await self._mkvpropedit.is_available():
            logger.info("metadata.mkvpropedit_unavailable", path=str(path))
            return False
        title = f"{job.title} ({job.year})" if job.title and job.year else job.title
        await self._mkvpropedit.apply(path, title=title, tracks=tracks)
        return True

    async def fetch_artwork(self, job: Job, dest_dir: Path) -> list[Path]:
        if job.tmdb_id is None:
            return []
        media_kind = "tv" if job.media_kind == MediaKind.SERIES else "movie"
        details = await self._tmdb.details(job.tmdb_id, media_kind=media_kind)
        if not details:
            return []

        saved: list[Path] = []
        for key, dest_name in (("poster_path", "poster.jpg"), ("backdrop_path", "fanart.jpg")):
            image_path = details.get(key)
            if not image_path:
                continue
            dest = dest_dir / dest_name
            if await self._tmdb.download_image(image_path, dest):
                saved.append(dest)
        return saved

    async def write_nfo(self, job: Job, dest: Path, *, config: LibraryConfig) -> Path | None:
        """Off by default — "Jellyfin's own TMDB lookup is usually better"
        (phase doc task 4)."""
        if not config.write_nfo:
            return None

        plot = ""
        if job.tmdb_id is not None:
            media_kind = "tv" if job.media_kind == MediaKind.SERIES else "movie"
            details = await self._tmdb.details(job.tmdb_id, media_kind=media_kind)
            plot = (details or {}).get("overview") or ""

        root_tag = "episodedetails" if job.media_kind == MediaKind.SERIES else "movie"
        lines = ["<?xml version='1.0' encoding='UTF-8'?>", f"<{root_tag}>"]
        lines.append(f"  <title>{escape(job.title or '')}</title>")
        if job.year is not None:
            lines.append(f"  <year>{job.year}</year>")
        if job.tmdb_id is not None:
            lines.append(f"  <tmdbid>{job.tmdb_id}</tmdbid>")
            lines.append(f'  <uniqueid type="tmdb">{job.tmdb_id}</uniqueid>')
        if job.imdb_id:
            lines.append(f"  <imdbid>{escape(job.imdb_id)}</imdbid>")
        if plot:
            lines.append(f"  <plot>{escape(plot)}</plot>")
        lines.append(f"</{root_tag}>")

        content = "\n".join(lines) + "\n"
        await asyncio.to_thread(_write_nfo_file, dest, content)
        return dest
