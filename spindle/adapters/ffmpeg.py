"""`ffprobe`-based output verification, per architecture.md §7.

`probe()` covers rip/transcode verification (phase 3/4) and now also lists
embedded subtitle streams (phase 5 task 1), so callers can record forced
tracks and decide which languages still need an external subtitle without a
second `ffprobe` invocation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CreateSubprocess = Callable[..., Awaitable[asyncio.subprocess.Process]]


@dataclass(frozen=True)
class SubtitleStreamInfo:
    index: int
    codec: str | None
    language: str | None
    is_forced: bool
    is_default: bool


@dataclass(frozen=True)
class MediaProbe:
    duration_seconds: float | None
    size_bytes: int
    has_video: bool
    video_codec: str | None
    width: int | None
    height: int | None
    subtitle_streams: tuple[SubtitleStreamInfo, ...] = ()
    audio_codec: str | None = None


class FfmpegAdapter:
    def __init__(
        self, *, create_subprocess: CreateSubprocess = asyncio.create_subprocess_exec
    ) -> None:
        self._create_subprocess = create_subprocess

    async def probe(self, path: Path) -> MediaProbe | None:
        """Best-effort `ffprobe`; never raises. `None` means "not a valid
        media file", which callers treat as a failed verification rather than
        a crash — per the "fail into needs_review" rule."""
        try:
            size_bytes = (await asyncio.to_thread(path.stat)).st_size
        except OSError:
            return None
        if size_bytes == 0:
            return None

        try:
            process = await self._create_subprocess(
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await process.communicate()
        except OSError:
            logger.warning("ffmpeg.probe_failed_to_start", path=str(path))
            return None

        if process.returncode != 0:
            return None

        try:
            payload = json.loads(stdout)
        except ValueError:
            return None

        return _parse_probe(payload, size_bytes=size_bytes)


def _parse_probe(payload: dict[str, Any], *, size_bytes: int) -> MediaProbe:
    fmt = payload.get("format") or {}
    duration_raw = fmt.get("duration")
    duration_seconds: float | None = None
    if duration_raw is not None:
        try:
            duration_seconds = float(duration_raw)
        except (TypeError, ValueError):
            duration_seconds = None

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subtitle_streams = tuple(
        SubtitleStreamInfo(
            index=int(s.get("index", i)),
            codec=s.get("codec_name"),
            language=(s.get("tags") or {}).get("language"),
            is_forced=bool((s.get("disposition") or {}).get("forced")),
            is_default=bool((s.get("disposition") or {}).get("default")),
        )
        for i, s in enumerate(streams)
        if s.get("codec_type") == "subtitle"
    )

    return MediaProbe(
        duration_seconds=duration_seconds,
        size_bytes=size_bytes,
        has_video=video is not None,
        video_codec=video.get("codec_name") if video else None,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        subtitle_streams=subtitle_streams,
        audio_codec=audio.get("codec_name") if audio else None,
    )
