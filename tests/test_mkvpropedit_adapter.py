from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from spindle.adapters.mkvpropedit import MkvPropEditAdapter, MkvPropEditError, TrackEdit


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _factory(
    process: _FakeProcess, calls: list[tuple[object, ...]]
) -> Callable[..., Awaitable[_FakeProcess]]:
    async def _create(*args: object, **_kwargs: object) -> _FakeProcess:
        calls.append(args)
        return process

    return _create


async def test_is_available_true_when_version_succeeds() -> None:
    process = _FakeProcess(returncode=0)
    adapter = MkvPropEditAdapter(create_subprocess=_factory(process, []))

    assert await adapter.is_available() is True


async def test_is_available_false_when_binary_missing() -> None:
    async def _create(*_args: object, **_kwargs: object) -> _FakeProcess:
        raise OSError("not found")

    adapter = MkvPropEditAdapter(create_subprocess=_create)

    assert await adapter.is_available() is False


async def test_apply_builds_title_and_track_edit_arguments(tmp_path: Path) -> None:
    process = _FakeProcess(returncode=0)
    calls: list[tuple[object, ...]] = []
    adapter = MkvPropEditAdapter(create_subprocess=_factory(process, calls))
    path = tmp_path / "movie.mkv"

    await adapter.apply(
        path,
        title="Amelie (2001)",
        tracks=[TrackEdit(track_id=1, track_type="s", language="deu", is_forced=True)],
    )

    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "mkvpropedit"
    assert str(path) in args
    assert "--edit" in args
    assert "info" in args
    assert "title=Amelie (2001)" in args
    assert "track:s1" in args
    assert "language=deu" in args
    assert "flag-forced=1" in args


async def test_apply_is_a_noop_with_no_title_or_tracks() -> None:
    process = _FakeProcess(returncode=0)
    calls: list[tuple[object, ...]] = []
    adapter = MkvPropEditAdapter(create_subprocess=_factory(process, calls))

    await adapter.apply(Path("movie.mkv"))

    assert calls == []


async def test_apply_raises_on_nonzero_exit(tmp_path: Path) -> None:
    process = _FakeProcess(returncode=2, stdout=b"error: no such track")
    adapter = MkvPropEditAdapter(create_subprocess=_factory(process, []))

    with pytest.raises(MkvPropEditError):
        await adapter.apply(tmp_path / "movie.mkv", title="X")
