"""`mkvpropedit` (MKVToolNix) wrapper — sets MKV-level metadata after
transcode, per phase-05-metadata-subtitles.md task 4: segment title and
per-track language/default/forced flags.

Optional external backend discovered at runtime, same degrade-gracefully
pattern as MakeMKV (CLAUDE.md §9 / architecture.md §7): if the binary isn't
installed, `MetadataService` logs and skips tagging rather than failing the
job — tagging is cosmetic, not load-bearing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from spindle.errors import AdapterError

CreateSubprocess = Callable[..., Awaitable[asyncio.subprocess.Process]]

_OUTPUT_TAIL_LINES = 20


class MkvPropEditError(AdapterError):
    """`mkvpropedit` failed."""


@dataclass(frozen=True)
class TrackEdit:
    """`track_id` is 1-based *within its type* — mkvpropedit's `track:s1`
    selector syntax, i.e. "the first subtitle track", not an absolute track
    number in the file."""

    track_id: int
    track_type: str  # "a" (audio), "s" (subtitle), "v" (video)
    language: str | None = None  # ISO 639-2, normalized by the caller
    is_default: bool | None = None
    is_forced: bool | None = None


class MkvPropEditAdapter:
    def __init__(
        self, *, create_subprocess: CreateSubprocess = asyncio.create_subprocess_exec
    ) -> None:
        self._create_subprocess = create_subprocess

    async def is_available(self) -> bool:
        try:
            process = await self._create_subprocess(
                "mkvpropedit",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await process.communicate()
        except OSError:
            return False
        return process.returncode == 0

    async def apply(
        self, path: Path, *, title: str | None = None, tracks: Sequence[TrackEdit] = ()
    ) -> None:
        args = _build_args(title=title, tracks=tracks)
        if not args:
            return

        try:
            process = await self._create_subprocess(
                "mkvpropedit",
                str(path),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _stderr = await process.communicate()
        except OSError as exc:
            raise MkvPropEditError(f"Cannot run mkvpropedit: {exc}") from exc

        if process.returncode != 0:
            output = stdout.decode("utf-8", errors="replace")
            tail = "\n".join(output.splitlines()[-_OUTPUT_TAIL_LINES:])
            raise MkvPropEditError(
                f"mkvpropedit exited with {process.returncode}", detail={"output_tail": tail}
            )


def _build_args(*, title: str | None, tracks: Sequence[TrackEdit]) -> list[str]:
    args: list[str] = []
    if title is not None:
        args += ["--edit", "info", "--set", f"title={title}"]
    for edit in tracks:
        edits = []
        if edit.language is not None:
            edits += ["--set", f"language={edit.language}"]
        if edit.is_default is not None:
            edits += ["--set", f"flag-default={1 if edit.is_default else 0}"]
        if edit.is_forced is not None:
            edits += ["--set", f"flag-forced={1 if edit.is_forced else 0}"]
        if edits:
            args += ["--edit", f"track:{edit.track_type}{edit.track_id}", *edits]
    return args
