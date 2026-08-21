"""`HandBrakeCLI` wrapper.

`scan_disc` (phase 3) is used for title selection when MakeMKV is absent.
`encode()`, encoder probing and `version()` are phase 4's additions, used by
`TranscodeStage` (`spindle/pipeline/stages.py`) and
`spindle/services/transcoder.py::choose_encoder`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spindle.errors import AdapterError
from spindle.utils.proc import kill_process_group, terminate_process_group

CreateSubprocess = Callable[..., Awaitable[asyncio.subprocess.Process]]

_OUTPUT_TAIL_LINES = 20
_JSON_TITLE_SET_MARKER = "JSON Title Set:"
_PROGRESS_JSON_MARKER = "Progress:"

# Hardware encoders need a probe (a listed encoder is not a working one);
# software encoders (x264/x265/...) always work if HandBrakeCLI itself runs.
_HARDWARE_ENCODERS = frozenset(
    {
        "nvenc_h264",
        "nvenc_h265",
        "qsv_h264",
        "qsv_h265",
        "vce_h264",
        "vce_h265",
        "vaapi_h264",
        "vaapi_h265",
    }
)

# Exit codes per phase-04-transcode-engine.md task 1.
_EXIT_MESSAGES = {
    1: "HandBrakeCLI was cancelled",
    2: "HandBrakeCLI reported invalid input",
    3: "HandBrakeCLI failed to initialize",
}

_LEGACY_PROGRESS_RE = re.compile(
    r"Encoding: task (\d+) of (\d+), ([\d.]+) %"
    r"(?: \(([\d.]+) fps, avg [\d.]+ fps, ETA (\d+)h(\d+)m(\d+)s\))?"
)


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


@dataclass(frozen=True)
class EncodeProgress:
    progress: float  # 0.0-1.0, overall across passes (PassID/PassCount-weighted)
    eta_seconds: int | None
    fps: float | None
    pass_id: int | None
    pass_count: int | None


OnEncodeProgress = Callable[[EncodeProgress], Awaitable[None]]


class HandBrakeAdapter:
    def __init__(
        self, *, create_subprocess: CreateSubprocess = asyncio.create_subprocess_exec
    ) -> None:
        self._create_subprocess = create_subprocess
        # Keyed by caller-supplied `handle` (a job id), same cancel-by-handle
        # pattern as `MakeMkvAdapter`.
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._encoder_cache: tuple[str, ...] | None = None

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

    async def version(self) -> str:
        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _stderr = await process.communicate()
        except OSError as exc:
            raise HandBrakeError(f"Cannot run HandBrakeCLI: {exc}") from exc
        if process.returncode != 0:
            raise HandBrakeError(f"HandBrakeCLI --version exited with {process.returncode}")
        return stdout.decode("utf-8", errors="replace").strip()

    async def available_encoders(self) -> tuple[str, ...]:
        """Encoders HandBrakeCLI lists *and* that actually work.

        Hardware encoders are probed with a 1-second null encode and the
        result cached for the adapter's lifetime — a listed encoder is not a
        working encoder (phase-04 task 1 gotcha).
        """
        if self._encoder_cache is not None:
            return self._encoder_cache

        listed = await self._list_encoders()
        working = []
        for name in listed:
            if name in _HARDWARE_ENCODERS:
                if await self._probe_encoder(name):
                    working.append(name)
            else:
                working.append(name)

        self._encoder_cache = tuple(working)
        return self._encoder_cache

    async def encode(
        self,
        src: Path,
        dst: Path,
        *,
        preset_file: Path,
        preset_name: str,
        overrides: Sequence[str] = (),
        on_progress: OnEncodeProgress | None = None,
        handle: int | None = None,
    ) -> None:
        await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)

        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--json",
                "--preset-import-file",
                str(preset_file),
                "--preset",
                preset_name,
                "-i",
                str(src),
                "-o",
                str(dst),
                *overrides,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise HandBrakeError(f"Cannot run HandBrakeCLI: {exc}") from exc

        if handle is not None:
            self._processes[handle] = process

        parser = _ProgressParser()
        lines: list[str] = []
        try:
            assert process.stdout is not None  # noqa: S101 -- PIPE requested above
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                lines.append(line)
                for progress in parser.feed(line):
                    if on_progress is not None:
                        await on_progress(progress)
            returncode = await process.wait()
        finally:
            if handle is not None:
                self._processes.pop(handle, None)

        if returncode != 0:
            tail = "\n".join(lines[-_OUTPUT_TAIL_LINES:])
            message = _EXIT_MESSAGES.get(returncode, f"HandBrakeCLI exited with {returncode}")
            raise HandBrakeError(message, detail={"output_tail": tail})

    async def cancel(self, handle: int, *, grace_period: float = 10.0) -> None:
        """SIGTERM the encode registered under `handle`, escalating to
        SIGKILL if it hasn't exited within `grace_period` seconds. A no-op if
        nothing is running under that handle."""
        process = self._processes.get(handle)
        if process is None:
            return
        terminate_process_group(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_period)
        except TimeoutError:
            kill_process_group(process)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5.0)

    async def _list_encoders(self) -> list[str]:
        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--encoder-list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _stderr = await process.communicate()
        except OSError as exc:
            raise HandBrakeError(f"Cannot run HandBrakeCLI: {exc}") from exc
        if process.returncode != 0:
            return []
        return [
            line.strip()
            for line in stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]

    async def _probe_encoder(self, name: str) -> bool:
        try:
            process = await self._create_subprocess(
                "HandBrakeCLI",
                "--json",
                "-e",
                name,
                "-i",
                os.devnull,
                "-o",
                os.devnull,
                "--stop-at",
                "seconds:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await process.communicate()
        except OSError:
            return False
        return process.returncode == 0


class _ProgressParser:
    """Stateful parser fed line-by-line from `encode()`'s stdout.

    HandBrake interleaves `Progress: {...}` JSON blocks with human-readable
    log lines on the same stream (same gotcha as `_extract_json_block`), and
    a pretty-printed block can span multiple lines, so lines are buffered
    until a balanced block closes. The legacy `Encoding: task ...` regex is
    only consulted once it's clear this run never produced JSON at all —
    "unavailable in the installed version", not "no progress on this line".
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._seen_json = False

    def feed(self, line: str) -> list[EncodeProgress]:
        self._buffer += line + "\n"
        results: list[EncodeProgress] = []

        while True:
            block, remainder = _extract_progress_block(self._buffer)
            if block is None:
                break
            self._buffer = remainder
            progress = _parse_json_progress(block)
            if progress is not None:
                self._seen_json = True
                results.append(progress)

        if results or self._seen_json:
            return results

        legacy = _parse_legacy_line(line)
        return [legacy] if legacy is not None else []


def _extract_progress_block(text: str) -> tuple[str | None, str]:
    marker_index = text.find(_PROGRESS_JSON_MARKER)
    if marker_index == -1:
        return None, text
    start = text.find("{", marker_index)
    if start == -1:
        return None, text

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
                return text[start : i + 1], text[i + 1 :]
    return None, text  # incomplete block; wait for more lines


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _overall_progress(progress: float, pass_id: Any, pass_count: Any) -> float:
    """HandBrake reports `Working.Progress` per *pass* — 0-1 twice for a
    two-pass preset (phase-04 gotcha). All four shipped presets are
    single-pass, making this a no-op, but weighting by `PassID`/`PassCount`
    keeps overall progress monotonic if a custom multi-pass preset is used."""
    count = _safe_int(pass_count)
    index = _safe_int(pass_id)
    if count is None or index is None or count <= 1:
        return min(1.0, max(0.0, progress))
    completed = max(index, 0)
    return min(1.0, max(0.0, (completed + progress) / count))


def _parse_json_progress(block: str) -> EncodeProgress | None:
    try:
        payload = json.loads(block)
    except ValueError:
        return None
    working = payload.get("Working")
    if not isinstance(working, dict):
        return None
    progress = _safe_float(working.get("Progress"))
    if progress is None:
        return None
    pass_id = working.get("PassID")
    pass_count = working.get("PassCount")
    return EncodeProgress(
        progress=_overall_progress(progress, pass_id, pass_count),
        eta_seconds=_safe_int(working.get("ETASeconds")),
        fps=_safe_float(working.get("Rate")),
        pass_id=_safe_int(pass_id),
        pass_count=_safe_int(pass_count),
    )


def _parse_legacy_line(line: str) -> EncodeProgress | None:
    match = _LEGACY_PROGRESS_RE.search(line)
    if match is None:
        return None
    percent = float(match.group(3)) / 100.0
    fps = float(match.group(4)) if match.group(4) else None
    eta_seconds = None
    if match.group(5) is not None:
        hours, minutes, seconds = (int(match.group(i)) for i in (5, 6, 7))
        eta_seconds = hours * 3600 + minutes * 60 + seconds
    return EncodeProgress(
        progress=min(1.0, max(0.0, percent)),
        eta_seconds=eta_seconds,
        fps=fps,
        pass_id=None,
        pass_count=None,
    )


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
