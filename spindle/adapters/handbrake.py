"""`HandBrakeCLI` wrapper.

Only the fallback scan path lands in this phase (`scan_disc`, used for title
selection when MakeMKV is absent — see the phase 3 design note on backends).
`encode()`, encoder probing and presets are phase 4's `HandBrakeAdapter`
additions; this class is extended there, not replaced.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from spindle.errors import AdapterError

CreateSubprocess = Callable[..., Awaitable[asyncio.subprocess.Process]]

_OUTPUT_TAIL_LINES = 20
_JSON_TITLE_SET_MARKER = "JSON Title Set:"


class HandBrakeError(AdapterError):
    """`HandBrakeCLI` failed or produced unparseable output."""


@dataclass(frozen=True)
class HandBrakeAudioTrack:
    index: int
    language: str | None
    codec: str | None


@dataclass(frozen=True)
class HandBrakeSubtitleTrack:
    index: int
    language: str | None


@dataclass(frozen=True)
class HandBrakeTitleInfo:
    title_id: int
    duration_seconds: int
    chapter_count: int
    audio_tracks: tuple[HandBrakeAudioTrack, ...]
    subtitle_tracks: tuple[HandBrakeSubtitleTrack, ...]


@dataclass(frozen=True)
class HandBrakeDiscScan:
    titles: tuple[HandBrakeTitleInfo, ...]


class HandBrakeAdapter:
    def __init__(
        self, *, create_subprocess: CreateSubprocess = asyncio.create_subprocess_exec
    ) -> None:
        self._create_subprocess = create_subprocess

    async def is_available(self) -> bool:
        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await process.communicate()
        except OSError:
            return False
        return process.returncode == 0

    async def scan_disc(self, device: str) -> HandBrakeDiscScan:
        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--input",
                device,
                "--scan",
                "--json",
                "--title",
                "0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _stderr = await process.communicate()
        except OSError as exc:
            raise HandBrakeError(f"Cannot run HandBrakeCLI: {exc}") from exc

        output = stdout.decode("utf-8", errors="replace")
        if process.returncode != 0:
            tail = "\n".join(output.splitlines()[-_OUTPUT_TAIL_LINES:])
            raise HandBrakeError(
                f"HandBrakeCLI exited with {process.returncode}", detail={"output_tail": tail}
            )

        block = _extract_json_block(output, _JSON_TITLE_SET_MARKER)
        if block is None:
            tail = "\n".join(output.splitlines()[-_OUTPUT_TAIL_LINES:])
            raise HandBrakeError(
                "HandBrakeCLI scan produced no title set", detail={"output_tail": tail}
            )

        try:
            payload = json.loads(block)
        except ValueError as exc:
            raise HandBrakeError(f"Could not parse HandBrakeCLI title set JSON: {exc}") from exc

        return _parse_title_set(payload)


def _extract_json_block(text: str, marker: str) -> str | None:
    """Pull the first balanced `{...}` object after `marker`.

    HandBrake interleaves human-readable log lines with the JSON block on the
    same stream (the same gotcha phase 4 documents for `--json` progress), so
    this can't be a per-line `json.loads` — it has to track brace depth
    (ignoring braces inside string literals) until the object closes.
    """
    marker_index = text.find(marker)
    if marker_index == -1:
        return None
    start = text.find("{", marker_index)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _duration_seconds(raw_title: dict[str, Any]) -> int:
    duration = raw_title.get("Duration") or {}
    return (
        int(duration.get("Hours", 0)) * 3600
        + int(duration.get("Minutes", 0)) * 60
        + int(duration.get("Seconds", 0))
    )


def _parse_title_set(payload: dict[str, Any]) -> HandBrakeDiscScan:
    titles = []
    for raw_title in payload.get("TitleList") or []:
        audio_tracks = tuple(
            HandBrakeAudioTrack(
                index=int(a.get("TrackNumber", i + 1)),
                language=a.get("LanguageCode") or a.get("Language"),
                codec=a.get("CodecName") or a.get("Codec"),
            )
            for i, a in enumerate(raw_title.get("AudioList") or [])
        )
        subtitle_tracks = tuple(
            HandBrakeSubtitleTrack(
                index=int(s.get("TrackNumber", i + 1)),
                language=s.get("LanguageCode") or s.get("Language"),
            )
            for i, s in enumerate(raw_title.get("SubtitleList") or [])
        )
        titles.append(
            HandBrakeTitleInfo(
                title_id=int(raw_title.get("Index", 0)),
                duration_seconds=_duration_seconds(raw_title),
                chapter_count=len(raw_title.get("ChapterList") or []),
                audio_tracks=audio_tracks,
                subtitle_tracks=subtitle_tracks,
            )
        )
    return HandBrakeDiscScan(titles=tuple(titles))
