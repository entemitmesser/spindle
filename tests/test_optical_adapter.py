from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from spindle.adapters.optical import DiscStatus, OpticalAdapter, TrayStatus
from spindle.errors import AdapterError

_DEV = "/dev/sr0"
_SECTOR = 2048


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _iso_dir_record(name: bytes, extent_lba: int, data_len: int, *, is_dir: bool) -> bytes:
    flags = 0x02 if is_dir else 0x00
    id_len = len(name)
    before_pad = 33 + id_len
    record = bytearray()
    record.append(before_pad + (before_pad % 2))
    record.append(0)
    record += _both32(extent_lba)
    record += _both32(data_len)
    record += bytes(7)
    record.append(flags)
    record.append(0)
    record.append(0)
    record += _both16(1)
    record.append(id_len)
    record += name
    if before_pad % 2 == 1:
        record.append(0)
    return bytes(record)


def _write_iso9660_image(path: Path) -> None:
    """A minimal real ISO9660 image on disk -- root dir has one child,
    `VIDEO_TS` -- written to a regular file so tests exercise the real
    `os.open`/`os.pread`/`os.close` path instead of a fake `SectorReader`."""
    sectors: dict[int, bytes] = {}

    root_record = _iso_dir_record(b"\x00", extent_lba=20, data_len=_SECTOR, is_dir=True)
    pvd = bytearray(_SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[40:72] = b"TESTMOVIE".ljust(32)
    pvd[156 : 156 + len(root_record)] = root_record
    sectors[16] = bytes(pvd)

    sectors[20] = (
        _iso_dir_record(b"\x00", extent_lba=20, data_len=_SECTOR, is_dir=True)
        + _iso_dir_record(b"\x01", extent_lba=20, data_len=_SECTOR, is_dir=True)
        + _iso_dir_record(b"VIDEO_TS", extent_lba=21, data_len=_SECTOR, is_dir=True)
    ).ljust(_SECTOR, b"\x00")

    image = bytearray((max(sectors) + 1) * _SECTOR)
    for lba, data in sectors.items():
        image[lba * _SECTOR : (lba + 1) * _SECTOR] = data
    path.write_bytes(bytes(image))


def test_open_failure_degrades_to_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """os.open() itself failing (e.g. permission denied) must degrade
    cleanly, not raise -- callers treat UNKNOWN as "can't tell right now"."""

    def _raise_open(*args: object, **kwargs: object) -> int:
        raise PermissionError("Permission denied")

    monkeypatch.setattr(os, "open", _raise_open)

    adapter = OpticalAdapter()
    assert adapter.drive_status(_DEV) == TrayStatus.UNKNOWN
    assert adapter.disc_status(_DEV) == DiscStatus.UNKNOWN


def test_ioctl_failure_after_successful_open_degrades_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression test for a real bug: if open() succeeds but the ioctl
    call raises OSError, `_open`'s contextmanager used to catch that
    exception (thrown back in at its `yield fd`) and try to `yield None` a
    second time -- which contextlib.contextmanager forbids and turns into
    `RuntimeError: generator didn't stop after throw()`. It must instead
    degrade to UNKNOWN just like an open-time failure does."""
    monkeypatch.setattr(os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(os, "close", lambda fd: None)
    monkeypatch.setattr(
        OpticalAdapter,
        "_ioctl",
        staticmethod(lambda fd, request, arg: (_ for _ in ()).throw(OSError("I/O error"))),
    )

    adapter = OpticalAdapter()
    assert adapter.drive_status(_DEV) == TrayStatus.UNKNOWN
    assert adapter.disc_status(_DEV) == DiscStatus.UNKNOWN


def test_eject_wraps_ioctl_failure_in_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(os, "close", lambda fd: None)
    monkeypatch.setattr(
        OpticalAdapter,
        "_ioctl",
        staticmethod(lambda fd, request, arg: (_ for _ in ()).throw(OSError("busy"))),
    )

    adapter = OpticalAdapter()
    with pytest.raises(AdapterError):
        adapter.eject(_DEV)


def test_eject_raises_adapter_error_when_device_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "open", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))

    adapter = OpticalAdapter()
    with pytest.raises(AdapterError):
        adapter.eject(_DEV)


def test_list_root_entries_reads_past_the_first_sector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression test for a real bug: `_open_filesystem` used to `return`
    the `DiscFilesystem` from inside `self._open`'s `with` block, so the fd
    was closed the instant it returned -- before `list_dir`'s lazy `read`
    callback (used for every sector past the volume descriptor) ever got a
    chance to run, raising `OSError: [Errno 9] Bad file descriptor`. Uses a
    real file and a real fd (not a mocked `os.open`) so the actual fd
    lifetime is exercised, not just the mock's bookkeeping.

    `os.pread` is POSIX-only, so on Windows (dev) it's shimmed via
    `lseek`+`read` on the same real fd -- equivalent for this single-threaded
    test, and irrelevant to the bug under test, which is about *when* the fd
    gets closed, not which syscall reads it.
    """
    if not hasattr(os, "pread"):

        def _pread(fd: int, length: int, offset: int) -> bytes:
            saved = os.lseek(fd, 0, os.SEEK_CUR)
            os.lseek(fd, offset, os.SEEK_SET)
            try:
                return os.read(fd, length)
            finally:
                os.lseek(fd, saved, os.SEEK_SET)

        monkeypatch.setattr(os, "pread", _pread, raising=False)

    image_path = tmp_path / "disc.iso"
    _write_iso9660_image(image_path)

    adapter = OpticalAdapter()
    assert adapter.read_volume_info(str(image_path)).volume_id == "TESTMOVIE"
    assert adapter.list_root_entries(str(image_path)) == ["VIDEO_TS"]
