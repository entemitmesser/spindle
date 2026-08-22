"""The only module that touches `/dev/sr*` directly.

Everything here is synchronous (ioctl/subprocess calls are cheap and brief,
apart from `read_dvd_title_info`'s `lsdvd` call); callers run it via
`asyncio.to_thread` rather than making the adapter itself async. Directory
reads never mount the disc — see `_discfs.py` for the mount-free ISO9660/UDF
parsers this module drives.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from spindle.adapters._discfs import (
    SECTOR_SIZE,
    DiscFilesystem,
    DiscFsEntry,
    open_disc_filesystem,
)
from spindle.errors import AdapterError

logger = structlog.get_logger(__name__)

_SYS_BLOCK = Path("/sys/block")

# linux/cdrom.h
_CDROMEJECT = 0x5309
_CDROMCLOSETRAY = 0x5319
_CDROM_DRIVE_STATUS = 0x5326
_CDROM_DISC_STATUS = 0x5327
_CDSL_CURRENT = 0x7FFFFFFE  # INT_MAX - 1


class TrayStatus(StrEnum):
    NO_DISC = "no_disc"
    TRAY_OPEN = "tray_open"
    NOT_READY = "not_ready"
    DISC_OK = "disc_ok"
    UNKNOWN = "unknown"


_DRIVE_STATUS_MAP = {
    1: TrayStatus.NO_DISC,
    2: TrayStatus.TRAY_OPEN,
    3: TrayStatus.NOT_READY,
    4: TrayStatus.DISC_OK,
}


class DiscStatus(StrEnum):
    NO_DISC = "no_disc"
    AUDIO = "audio"
    DATA = "data"
    MIXED = "mixed"
    UNKNOWN = "unknown"


_DISC_STATUS_MAP = {
    1: DiscStatus.NO_DISC,
    100: DiscStatus.AUDIO,
    101: DiscStatus.DATA,
    102: DiscStatus.DATA,
    103: DiscStatus.DATA,
    104: DiscStatus.DATA,
    105: DiscStatus.MIXED,
}


@dataclass(frozen=True)
class DriveInfo:
    device_path: str
    model: str | None
    vendor: str | None


@dataclass(frozen=True)
class VolumeInfo:
    volume_id: str | None


@dataclass(frozen=True)
class DvdTitleInfo:
    index: int
    length_seconds: float


class OpticalAdapter:
    """Wraps `/dev/sr*` ioctls, mount-free directory reads, and `lsdvd`."""

    def enumerate_drives(self) -> list[DriveInfo]:
        if not _SYS_BLOCK.is_dir():
            return []
        drives = []
        for entry in sorted(_SYS_BLOCK.iterdir()):
            if not entry.name.startswith("sr"):
                continue
            device_dir = entry / "device"
            drives.append(
                DriveInfo(
                    device_path=f"/dev/{entry.name}",
                    model=_read_sysfs_attr(device_dir / "model"),
                    vendor=_read_sysfs_attr(device_dir / "vendor"),
                )
            )
        return drives

    def drive_status(self, dev: str) -> TrayStatus:
        with self._open(dev) as fd:
            if fd is None:
                return TrayStatus.UNKNOWN
            try:
                code = self._ioctl(fd, _CDROM_DRIVE_STATUS, _CDSL_CURRENT)
            except OSError as exc:
                logger.warning("optical.ioctl_failed", dev=dev, error=str(exc))
                return TrayStatus.UNKNOWN
            return _DRIVE_STATUS_MAP.get(code, TrayStatus.UNKNOWN)

    def disc_status(self, dev: str) -> DiscStatus:
        with self._open(dev) as fd:
            if fd is None:
                return DiscStatus.UNKNOWN
            try:
                code = self._ioctl(fd, _CDROM_DISC_STATUS, _CDSL_CURRENT)
            except OSError as exc:
                logger.warning("optical.ioctl_failed", dev=dev, error=str(exc))
                return DiscStatus.UNKNOWN
            return _DISC_STATUS_MAP.get(code, DiscStatus.UNKNOWN)

    def disc_size_bytes(self, dev: str) -> int | None:
        """Media size from sysfs (works for ISO9660 and UDF/Blu-ray alike)."""
        name = Path(dev).name
        raw = _read_sysfs_attr(_SYS_BLOCK / name / "size")
        if raw is None:
            return None
        with contextlib.suppress(ValueError):
            return int(raw) * 512
        return None

    def read_volume_info(self, dev: str) -> VolumeInfo:
        with self._open_filesystem(dev) as fs:
            return VolumeInfo(volume_id=fs.volume_id if fs is not None else None)

    def list_root_entries(self, dev: str) -> list[str]:
        with self._open_filesystem(dev) as fs:
            if fs is None:
                return []
            return [e.name for e in fs.list_dir([])]

    def list_entries(self, dev: str, path: Sequence[str]) -> list[DiscFsEntry]:
        """Mount-free listing of a nested directory (e.g. `("BDMV", "PLAYLIST")`)."""
        with self._open_filesystem(dev) as fs:
            if fs is None:
                return []
            return [fs.stat(e) for e in fs.list_dir(path)]

    def read_dvd_title_info(
        self,
        dev: str,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> list[DvdTitleInfo]:
        """Title count + per-title duration via `lsdvd -Oy`, for fingerprinting."""
        try:
            result = runner(
                ["lsdvd", "-Oy", dev],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return _parse_lsdvd_output(result.stdout)

    def eject(self, dev: str) -> None:
        with self._open(dev) as fd:
            if fd is None:
                raise AdapterError(f"Cannot open {dev} to eject")
            try:
                self._ioctl(fd, _CDROMEJECT, 0)
            except OSError as exc:
                raise AdapterError(f"Cannot eject {dev}: {exc}") from exc

    def close_tray(self, dev: str) -> None:
        with self._open(dev) as fd:
            if fd is None:
                raise AdapterError(f"Cannot open {dev} to close tray")
            try:
                self._ioctl(fd, _CDROMCLOSETRAY, 0)
            except OSError as exc:
                raise AdapterError(f"Cannot close tray {dev}: {exc}") from exc

    # -- internals ---------------------------------------------------------

    @contextlib.contextmanager
    def _open(self, dev: str) -> Iterator[int | None]:
        try:
            # O_NONBLOCK: an open() on an empty/spinning-up drive otherwise
            # blocks until media is present, which would hang the caller.
            fd = os.open(dev, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except OSError as exc:
            # Most commonly a host/container GID mismatch on the device node
            # (see docker/entrypoint.sh) — logged here since callers degrade
            # this to a generic UNKNOWN status with no other trace of why.
            logger.warning("optical.open_failed", dev=dev, error=str(exc), errno=exc.errno)
            yield None
            return
        try:
            yield fd
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def _open_filesystem(self, dev: str) -> Iterator[DiscFilesystem | None]:
        # `list_dir`/`stat` on the returned filesystem read lazily via the
        # `read` closure below, so the fd must stay open for as long as the
        # caller holds the filesystem object -- yielding it from inside this
        # `with` (rather than returning it after `_open`'s `with` has already
        # closed the fd) is what keeps that true.
        with self._open(dev) as fd:
            if fd is None:
                yield None
                return

            def read(lba: int) -> bytes:
                assert fd is not None  # noqa: S101 -- narrows the enclosing Optional for mypy
                return os.pread(fd, SECTOR_SIZE, lba * SECTOR_SIZE)

            yield open_disc_filesystem(read)

    @staticmethod
    def _ioctl(fd: int, request: int, arg: int) -> int:
        fcntl = importlib.import_module("fcntl")  # POSIX-only; imported lazily for Windows dev
        return int(fcntl.ioctl(fd, request, arg))


def _read_sysfs_attr(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


_LSDVD_TRACK_LENGTH = re.compile(r"\blength\s*:\s*([\d.]+)")


def _parse_lsdvd_output(output: str) -> list[DvdTitleInfo]:
    """Parse `lsdvd -Oy`'s Ruby-literal-ish output for per-track lengths.

    Only top-level `track` entries carry a `length` field (nested `audio`/
    `subp` entries don't), so a flat regex scan is enough — no need for a
    full Ruby-literal parser.
    """
    return [
        DvdTitleInfo(index=i, length_seconds=float(match.group(1)))
        for i, match in enumerate(_LSDVD_TRACK_LENGTH.finditer(output), start=1)
    ]
