from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from spindle.adapters.makemkv import (
    MakeMkvAdapter,
    MakeMkvError,
    RipProgress,
)
from spindle.config import RipConfig
from spindle.models.job import MediaKind
from spindle.services.ripper import TitleCandidate, select_titles


def _seconds_to_hms(total: int) -> str:
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _title_lines(
    title_id: int,
    *,
    name: str,
    chapters: int,
    duration_seconds: int,
    size_bytes: int,
    streams: list[tuple[str, str | None]],
) -> list[str]:
    lines = [
        f'TINFO:{title_id},2,0,"{name}"',
        f'TINFO:{title_id},8,0,"{chapters}"',
        f'TINFO:{title_id},9,0,"{_seconds_to_hms(duration_seconds)}"',
        f'TINFO:{title_id},11,0,"{size_bytes}"',
        f'TINFO:{title_id},27,0,"title_t{title_id:02d}.mkv"',
    ]
    for stream_idx, (stream_type, language) in enumerate(streams):
        lines.append(f'SINFO:{title_id},{stream_idx},1,0,"{stream_type}"')
        if language is not None:
            lines.append(f'SINFO:{title_id},{stream_idx},3,0,"{language}"')
    return lines


def _build_playlist_obfuscation_fixture() -> str:
    lines = _title_lines(
        0,
        name="Main Movie",
        chapters=24,
        duration_seconds=7200,
        size_bytes=30_000_000_000,
        streams=[("Video", None), ("Audio", "eng")],
    )
    for i in range(1, 41):
        lines += _title_lines(
            i,
            name=f"struct_{i}",
            chapters=150,
            duration_seconds=3600 + i * 5,
            size_bytes=500_000_000,
            streams=[("Video", None)],
        )
    return "\n".join(lines)


class _FakeBufferedProcess:
    """Stand-in for `asyncio.subprocess.Process` for `communicate()`-style calls."""

    def __init__(self, *, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self._stdout = stdout.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _buffered_factory(
    process: _FakeBufferedProcess,
) -> Callable[..., Awaitable[_FakeBufferedProcess]]:
    async def _create(*_args: object, **_kwargs: object) -> _FakeBufferedProcess:
        return process

    return _create


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)

    def close(self) -> None:
        self._queue.put_nowait(None)

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeStreamingProcess:
    """Stand-in for `asyncio.subprocess.Process` for `rip_title`'s streamed reads."""

    def __init__(self, lines: list[bytes], *, auto_close: bool = True, returncode: int = 0) -> None:
        self.pid = 4321
        self.returncode: int | None = None
        self.stdout = _FakeStdout(lines)
        self.terminate_calls = 0
        self.kill_calls = 0
        self._done = asyncio.Event()
        if auto_close:
            self.stdout.close()
            self.returncode = returncode
            self._done.set()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15
        self.stdout.close()
        self._done.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.stdout.close()
        self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode


def _streaming_factory(
    process: _FakeStreamingProcess,
) -> Callable[..., Awaitable[_FakeStreamingProcess]]:
    async def _create(*_args: object, **_kwargs: object) -> _FakeStreamingProcess:
        return process

    return _create


async def test_scan_parses_fixture_and_select_titles_finds_the_main_feature() -> None:
    fixture = _build_playlist_obfuscation_fixture()
    process = _FakeBufferedProcess(returncode=0, stdout=fixture)
    adapter = MakeMkvAdapter(create_subprocess=_buffered_factory(process))

    scan = await adapter.scan("/dev/sr0")

    assert len(scan.titles) == 41
    main = next(t for t in scan.titles if t.title_id == 0)
    assert main.name == "Main Movie"
    assert main.chapter_count == 24
    assert main.duration_seconds == 7200
    assert main.size_bytes == 30_000_000_000
    assert main.output_filename == "title_t00.mkv"
    assert [s.stream_type for s in main.streams] == ["Video", "Audio"]
    assert main.streams[1].language == "eng"

    candidates = [
        TitleCandidate(t.title_id, t.duration_seconds, t.chapter_count, t.size_bytes)
        for t in scan.titles
    ]
    result = select_titles(candidates, config=RipConfig(), media_kind=MediaKind.MOVIE)

    assert result.needs_review is False
    assert [c.title_id for c in result.selected] == [0]


async def test_scan_raises_on_nonzero_exit() -> None:
    process = _FakeBufferedProcess(returncode=1, stdout='MSG:1000,0,0,"disc read error"')
    adapter = MakeMkvAdapter(create_subprocess=_buffered_factory(process))

    with pytest.raises(MakeMkvError):
        await adapter.scan("/dev/sr0")


async def test_scan_raises_a_clear_error_on_expired_key_message() -> None:
    output = 'MSG:5021,0,1,"Your beta key has expired, please visit MakeMKV forum"'
    process = _FakeBufferedProcess(returncode=0, stdout=output)
    adapter = MakeMkvAdapter(create_subprocess=_buffered_factory(process))

    with pytest.raises(MakeMkvError, match="MakeMKV key expired or missing"):
        await adapter.scan("/dev/sr0")


async def test_is_available_false_on_nonzero_exit() -> None:
    process = _FakeBufferedProcess(returncode=1, stdout="")
    adapter = MakeMkvAdapter(create_subprocess=_buffered_factory(process))

    assert await adapter.is_available() is False


async def test_is_available_false_when_binary_missing() -> None:
    async def _create(*_args: object, **_kwargs: object) -> _FakeBufferedProcess:
        raise OSError("not found")

    adapter = MakeMkvAdapter(create_subprocess=_create)

    assert await adapter.is_available() is False


async def test_rip_title_streams_progress_and_resolves_output_path(tmp_path: Path) -> None:
    lines = [
        b'PRGT:0,0,"Saving to MKV file"\n',
        b"PRGV:0,50,100\n",
        b"PRGV:0,100,100\n",
    ]
    process = _FakeStreamingProcess(lines)
    adapter = MakeMkvAdapter(create_subprocess=_streaming_factory(process))
    (tmp_path / "title_t00.mkv").write_bytes(b"fake mkv data")

    events: list[RipProgress] = []

    async def on_progress(progress: RipProgress) -> None:
        events.append(progress)

    result = await adapter.rip_title("/dev/sr0", 0, tmp_path, on_progress)

    assert result == tmp_path / "title_t00.mkv"
    assert [e.progress for e in events] == [0.5, 1.0]
    assert all(e.message == "Saving to MKV file" for e in events)


async def test_rip_title_raises_on_nonzero_exit(tmp_path: Path) -> None:
    process = _FakeStreamingProcess([b'MSG:1000,0,0,"disc read error"\n'], returncode=3)
    adapter = MakeMkvAdapter(create_subprocess=_streaming_factory(process))

    with pytest.raises(MakeMkvError):
        await adapter.rip_title("/dev/sr0", 0, tmp_path)


async def test_cancel_terminates_the_registered_process(tmp_path: Path) -> None:
    process = _FakeStreamingProcess([], auto_close=False)
    adapter = MakeMkvAdapter(create_subprocess=_streaming_factory(process))

    rip_task = asyncio.create_task(adapter.rip_title("/dev/sr0", 0, tmp_path, handle=99))
    # `rip_title` hops through a thread (the `mkdir`) before registering its
    # process, so a single `sleep(0)` isn't reliably enough to observe that
    # registration — poll for it instead of racing.
    for _ in range(200):
        if adapter._processes.get(99) is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("rip_title never registered its process under handle 99")

    await adapter.cancel(99)

    with pytest.raises(MakeMkvError):
        await rip_task
    # No os.killpg on this (Windows) dev box, same as OpticalAdapter's ioctls —
    # falls back to Process.terminate(), which is what matters for the test.
    assert process.terminate_calls == 1


async def test_cancel_is_a_noop_for_an_unknown_handle() -> None:
    process = _FakeStreamingProcess([])
    adapter = MakeMkvAdapter(create_subprocess=_streaming_factory(process))

    await adapter.cancel(12345)  # must not raise

    assert process.terminate_calls == 0
