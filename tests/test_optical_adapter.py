from __future__ import annotations

import os

import pytest

from spindle.adapters.optical import DiscStatus, OpticalAdapter, TrayStatus
from spindle.errors import AdapterError

_DEV = "/dev/sr0"


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
