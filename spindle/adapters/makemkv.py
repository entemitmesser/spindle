"""`makemkvcon` robot-mode wrapper, per phase-03-rip-engine.md task 1.

Unlike `OpticalAdapter` (cheap, brief ioctls run via `asyncio.to_thread`),
`scan`/`rip_title` here drive a genuinely long-running subprocess, so per
`CLAUDE.md` rule 4 they're `async def` over `asyncio.create_subprocess_exec`,
streamed rather than `communicate()`-buffered where progress matters.

Robot-mode output is CSV with quoted, comma-containing fields, so every line
is parsed with `csv.reader` rather than `split(",")` (a title name like
`"Alice, Through the Looking Glass"` would otherwise split wrong).

MakeMKV doesn't publish a stable message-code reference, so `_msg_failure`
doesn't guess at numeric codes for "this rip is broken" — the subprocess's
own exit code is the authoritative failure signal. The one exception is the
beta-key gotcha from the phase doc, which is real, user-actionable, and
detectable from the message text regardless of exit code.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from spindle.errors import AdapterError

logger = structlog.get_logger(__name__)

CreateSubprocess = Callable[..., Awaitable[asyncio.subprocess.Process]]

# TINFO attribute ids (spindle/docs/phases/phase-03-rip-engine.md task 1)
_TINFO_NAME = 2
_TINFO_CHAPTER_COUNT = 8
_TINFO_DURATION = 9
_TINFO_SIZE_BYTES = 11
_TINFO_OUTPUT_FILENAME = 27

# SINFO attribute ids
_SINFO_TYPE = 1
_SINFO_LANGUAGE = 3

_KEY_FAILURE_MARKERS = ("expired", "not valid", "no valid key", "missing")

_OUTPUT_TAIL_LINES = 20


class MakeMkvError(AdapterError):
    """`makemkvcon` failed or reported a fatal condition."""


@dataclass(frozen=True)
class MakeMkvStreamInfo:
    stream_id: int
    stream_type: str | None
    language: str | None


@dataclass(frozen=True)
class MakeMkvTitleInfo:
    title_id: int
    name: str | None
    duration_seconds: int
    size_bytes: int
    chapter_count: int
    output_filename: str | None
    streams: tuple[MakeMkvStreamInfo, ...]


@dataclass(frozen=True)
class DiscScan:
    titles: tuple[MakeMkvTitleInfo, ...]


@dataclass(frozen=True)
class RipProgress:
    progress: float  # 0.0-1.0, overall (PRGV's total/max, not current sub-op)
    message: str | None


OnProgress = Callable[[RipProgress], Awaitable[None]]


class MakeMkvAdapter:
    def __init__(
        self, *, create_subprocess: CreateSubprocess = asyncio.create_subprocess_exec
    ) -> None:
        self._create_subprocess = create_subprocess
        # Keyed by caller-supplied `handle` (a job id) so `cancel()` can find
        # and kill the right in-flight rip without RipStage ever touching a
        # subprocess itself (CLAUDE.md rule 3).
        self._processes: dict[int, asyncio.subprocess.Process] = {}

    async def is_available(self) -> bool:
        try:
            process = await self._create_subprocess(
                "makemkvcon",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await process.communicate()
        except OSError:
            return False
        return process.returncode == 0

    async def scan(self, device: str) -> DiscScan:
        output = await self._run_robot("-r", "--cache=1", "info", f"dev:{device}")
        return _parse_scan_output(output)

    async def rip_title(
        self,
        device: str,
        title_id: int,
        out_dir: Path,
        on_progress: OnProgress | None = None,
        *,
        handle: int | None = None,
    ) -> Path:
        """Rip `title_id` into `out_dir`. `--noscan` matters — without it
        MakeMKV rescans the disc and doubles the time (phase doc gotcha).

        `handle` (typically the job id) registers the subprocess so a
        concurrent `cancel(handle)` call can kill it; when the process dies,
        `process.stdout` hits EOF and this coroutine unwinds and raises on
        its own, so no extra cancellation plumbing is needed here.
        """
        await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)

        try:
            process = await self._create_subprocess(
                "makemkvcon",
                "-r",
                "--progress=-same",
                "--noscan",
                "mkv",
                f"dev:{device}",
                str(title_id),
                str(out_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise MakeMkvError(f"Cannot run makemkvcon: {exc}") from exc

        if handle is not None:
            self._processes[handle] = process

        try:
            lines: list[str] = []
            failures: list[str] = []
            current_message: str | None = None
            assert process.stdout is not None  # noqa: S101 -- PIPE requested above

            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                lines.append(line)
                parsed = _parse_robot_line(line)
                if parsed is None:
                    continue
                record_type, fields = parsed

                if record_type in ("PRGT", "PRGC") and len(fields) >= 3 and fields[2]:
                    current_message = fields[2]
                elif record_type == "PRGV":
                    progress = _prgv_progress(fields)
                    if progress is not None and on_progress is not None:
                        await on_progress(RipProgress(progress=progress, message=current_message))
                elif record_type == "MSG":
                    failure = _msg_failure(fields)
                    if failure is not None:
                        failures.append(failure)

            returncode = await process.wait()
        finally:
            if handle is not None:
                self._processes.pop(handle, None)

        tail = "\n".join(lines[-_OUTPUT_TAIL_LINES:])

        if returncode != 0:
            raise MakeMkvError(f"makemkvcon exited with {returncode}", detail={"output_tail": tail})
        if failures:
            raise MakeMkvError(failures[-1], detail={"output_tail": tail})

        return _resolve_output_path(out_dir)

    async def cancel(self, handle: int, *, grace_period: float = 10.0) -> None:
        """SIGTERM the rip registered under `handle`, escalating to SIGKILL
        if it hasn't exited within `grace_period` seconds. A no-op if
        nothing is running under that handle (already finished, or never
        started)."""
        process = self._processes.get(handle)
        if process is None:
            return
        _terminate_process_group(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_period)
        except TimeoutError:
            _kill_process_group(process)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5.0)

    async def _run_robot(self, *args: str) -> str:
        try:
            process = await self._create_subprocess(
                "makemkvcon",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _stderr = await process.communicate()
        except OSError as exc:
            raise MakeMkvError(f"Cannot run makemkvcon: {exc}") from exc

        output = stdout.decode("utf-8", errors="replace")
        if process.returncode != 0:
            tail = "\n".join(output.splitlines()[-_OUTPUT_TAIL_LINES:])
            raise MakeMkvError(
                f"makemkvcon exited with {process.returncode}", detail={"output_tail": tail}
            )
        return output


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill the whole process group started with `start_new_session=True`, so
    a `makemkvcon` that forked helpers doesn't leave orphans holding the
    drive. `os.killpg`/`os.getpgid` don't exist on Windows (this dev
    machine); `Process.terminate()` is the cross-platform fallback, same
    pattern as `OpticalAdapter`'s POSIX-only ioctls."""
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        with contextlib.suppress(ProcessLookupError):
            killpg(getpgid(process.pid), signal.SIGTERM)
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        with contextlib.suppress(ProcessLookupError):
            killpg(getpgid(process.pid), signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _parse_robot_line(line: str) -> tuple[str, list[str]] | None:
    line = line.rstrip("\r\n")
    if not line or ":" not in line:
        return None
    record_type, rest = line.split(":", 1)
    fields = next(csv.reader([rest]), [])
    return record_type, fields


def _prgv_progress(fields: list[str]) -> float | None:
    if len(fields) < 3:
        return None
    try:
        _current, total, maximum = int(fields[0]), int(fields[1]), int(fields[2])
    except ValueError:
        return None
    if maximum <= 0:
        return None
    return min(1.0, max(0.0, total / maximum))


def _msg_failure(fields: list[str]) -> str | None:
    if len(fields) < 4:
        return None
    message = fields[3]
    lowered = message.lower()
    if "key" in lowered and any(marker in lowered for marker in _KEY_FAILURE_MARKERS):
        return "MakeMKV key expired or missing"
    return None


def _parse_hms(value: str | None) -> int | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_scan_output(output: str) -> DiscScan:
    title_attrs: dict[int, dict[int, str]] = {}
    stream_attrs: dict[tuple[int, int], dict[int, str]] = {}
    failures: list[str] = []

    for line in output.splitlines():
        parsed = _parse_robot_line(line)
        if parsed is None:
            continue
        record_type, fields = parsed

        if record_type == "TINFO" and len(fields) >= 4:
            title_id, attr_id = _parse_int(fields[0]), _parse_int(fields[1])
            if title_id is None or attr_id is None:
                continue
            title_attrs.setdefault(title_id, {})[attr_id] = fields[3]

        elif record_type == "SINFO" and len(fields) >= 5:
            title_id, stream_idx, attr_id = (
                _parse_int(fields[0]),
                _parse_int(fields[1]),
                _parse_int(fields[2]),
            )
            if title_id is None or stream_idx is None or attr_id is None:
                continue
            stream_attrs.setdefault((title_id, stream_idx), {})[attr_id] = fields[4]

        elif record_type == "MSG":
            failure = _msg_failure(fields)
            if failure is not None:
                failures.append(failure)

    if failures:
        tail = "\n".join(output.splitlines()[-_OUTPUT_TAIL_LINES:])
        raise MakeMkvError(failures[-1], detail={"output_tail": tail})

    streams_by_title: dict[int, list[MakeMkvStreamInfo]] = {}
    for (title_id, stream_idx), attrs in sorted(stream_attrs.items()):
        streams_by_title.setdefault(title_id, []).append(
            MakeMkvStreamInfo(
                stream_id=stream_idx,
                stream_type=attrs.get(_SINFO_TYPE),
                language=attrs.get(_SINFO_LANGUAGE),
            )
        )

    titles = [
        MakeMkvTitleInfo(
            title_id=title_id,
            name=attrs.get(_TINFO_NAME),
            duration_seconds=_parse_hms(attrs.get(_TINFO_DURATION)) or 0,
            size_bytes=_parse_int(attrs.get(_TINFO_SIZE_BYTES)) or 0,
            chapter_count=_parse_int(attrs.get(_TINFO_CHAPTER_COUNT)) or 0,
            output_filename=attrs.get(_TINFO_OUTPUT_FILENAME),
            streams=tuple(streams_by_title.get(title_id, [])),
        )
        for title_id, attrs in sorted(title_attrs.items())
    ]
    return DiscScan(titles=tuple(titles))


def _resolve_output_path(out_dir: Path) -> Path:
    """MakeMKV names the output file itself; glob rather than assume a name
    (phase doc gotcha)."""
    candidates = sorted(out_dir.glob("*.mkv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise MakeMkvError(f"makemkvcon reported success but wrote no MKV file to {out_dir}")
    return candidates[0]
