from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from spindle.adapters.handbrake import EncodeProgress, HandBrakeAdapter, HandBrakeError

_TITLE_SET = {
    "TitleList": [
        {
            "Index": 1,
            "Duration": {"Hours": 1, "Minutes": 32, "Seconds": 10},
            "ChapterList": [{"Name": "Chapter 1"}, {"Name": "Chapter 2"}],
            "AudioList": [{"TrackNumber": 1, "LanguageCode": "eng", "CodecName": "AC3"}],
            "SubtitleList": [{"TrackNumber": 1, "LanguageCode": "deu"}],
        },
        {
            "Index": 2,
            "Duration": {"Hours": 0, "Minutes": 5, "Seconds": 0},
            "ChapterList": [],
            "AudioList": [],
            "SubtitleList": [],
        },
    ]
}


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self._stdout = stdout.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _factory(process: _FakeProcess) -> Callable[..., Awaitable[_FakeProcess]]:
    async def _create(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    return _create


def _scan_output() -> str:
    # HandBrakeCLI interleaves human-readable log lines with the JSON block
    # on the same stream — the fixture reproduces that instead of emitting
    # bare JSON, to actually exercise the balanced-brace extraction.
    return (
        "[00:00:00] hb_init: starting libhb thread\n"
        "libhb: scan thread found 2 valid title(s)\n"
        "JSON Title Set: " + json.dumps(_TITLE_SET) + "\n"
        "[00:00:01] libhb: scan complete\n"
    )


async def test_scan_disc_parses_the_json_title_set_block() -> None:
    process = _FakeProcess(returncode=0, stdout=_scan_output())
    adapter = HandBrakeAdapter(create_subprocess=_factory(process))

    scan = await adapter.scan_disc("/dev/sr0")

    assert len(scan.titles) == 2
    main = scan.titles[0]
    assert main.title_id == 1
    assert main.duration_seconds == 1 * 3600 + 32 * 60 + 10
    assert main.chapter_count == 2


async def test_scan_disc_audio_and_subtitle_tracks() -> None:
    process = _FakeProcess(returncode=0, stdout=_scan_output())
    adapter = HandBrakeAdapter(create_subprocess=_factory(process))

    scan = await adapter.scan_disc("/dev/sr0")

    main = scan.titles[0]
    assert len(main.audio_tracks) == 1
    assert main.audio_tracks[0].language == "eng"
    assert main.audio_tracks[0].codec == "AC3"
    assert len(main.subtitle_tracks) == 1
    assert main.subtitle_tracks[0].language == "deu"


async def test_scan_disc_raises_on_nonzero_exit() -> None:
    process = _FakeProcess(returncode=1, stdout="Error: unable to open source\n")
    adapter = HandBrakeAdapter(create_subprocess=_factory(process))

    with pytest.raises(HandBrakeError):
        await adapter.scan_disc("/dev/sr0")


async def test_scan_disc_raises_when_no_title_set_present() -> None:
    process = _FakeProcess(returncode=0, stdout="nothing useful here\n")
    adapter = HandBrakeAdapter(create_subprocess=_factory(process))

    with pytest.raises(HandBrakeError):
        await adapter.scan_disc("/dev/sr0")


async def test_is_available_false_when_binary_missing() -> None:
    async def _create(*_args: object, **_kwargs: object) -> _FakeProcess:
        raise OSError("not found")

    adapter = HandBrakeAdapter(create_subprocess=_create)

    assert await adapter.is_available() is False


async def test_version_returns_trimmed_stdout() -> None:
    process = _FakeProcess(returncode=0, stdout="HandBrake 1.7.3\n")
    adapter = HandBrakeAdapter(create_subprocess=_factory(process))

    assert await adapter.version() == "HandBrake 1.7.3"


class _QueuedFactory:
    """Returns fake processes in call order, so a test can script a
    multi-subprocess sequence (list encoders, then probe each one)."""

    def __init__(self, processes: list[Any]) -> None:
        self._processes = list(processes)

    async def __call__(self, *_args: object, **_kwargs: object) -> Any:
        return self._processes.pop(0)


async def test_available_encoders_filters_out_a_hardware_encoder_that_fails_its_probe() -> None:
    listed = _FakeProcess(returncode=0, stdout="x264\nx265\nnvenc_h265\n")
    failed_probe = _FakeProcess(returncode=1, stdout="")
    adapter = HandBrakeAdapter(create_subprocess=_QueuedFactory([listed, failed_probe]))

    encoders = await adapter.available_encoders()

    assert encoders == ("x264", "x265")


async def test_available_encoders_keeps_a_hardware_encoder_that_passes_its_probe() -> None:
    listed = _FakeProcess(returncode=0, stdout="x265\nnvenc_h265\n")
    ok_probe = _FakeProcess(returncode=0, stdout="")
    adapter = HandBrakeAdapter(create_subprocess=_QueuedFactory([listed, ok_probe]))

    encoders = await adapter.available_encoders()

    assert encoders == ("x265", "nvenc_h265")


async def test_available_encoders_on_a_machine_without_hardware_falls_back_to_software_only() -> (
    None
):
    listed = _FakeProcess(returncode=0, stdout="x264\nx265\nnvenc_h265\nqsv_h265\nvce_h265\n")
    probes = [_FakeProcess(returncode=1, stdout="") for _ in range(3)]
    adapter = HandBrakeAdapter(create_subprocess=_QueuedFactory([listed, *probes]))

    encoders = await adapter.available_encoders()

    assert encoders == ("x264", "x265")


async def test_available_encoders_caches_the_result() -> None:
    listed = _FakeProcess(returncode=0, stdout="x265\n")
    calls = 0

    async def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
        nonlocal calls
        calls += 1
        return listed

    adapter = HandBrakeAdapter(create_subprocess=factory)

    first = await adapter.available_encoders()
    second = await adapter.available_encoders()

    assert first == second == ("x265",)
    assert calls == 1


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


async def test_encode_parses_json_progress_lines(tmp_path: Path) -> None:
    lines = [
        b'Progress: {"State":"WORKING","Working":{"Progress":0.25,"ETASeconds":300,'
        b'"Rate":24.1,"PassID":0,"PassCount":1}}\n',
        b'Progress: {"State":"WORKING","Working":{"Progress":1.0,"ETASeconds":0,'
        b'"Rate":25.0,"PassID":0,"PassCount":1}}\n',
    ]
    process = _FakeStreamingProcess(lines)
    adapter = HandBrakeAdapter(create_subprocess=_streaming_factory(process))

    events: list[EncodeProgress] = []

    async def on_progress(progress: EncodeProgress) -> None:
        events.append(progress)

    await adapter.encode(
        tmp_path / "src.mkv",
        tmp_path / "out" / "dst.mkv",
        preset_file=tmp_path / "preset.json",
        preset_name="Spindle 1080p H.265",
        on_progress=on_progress,
    )

    assert [e.progress for e in events] == [0.25, 1.0]
    assert events[0].eta_seconds == 300
    assert events[0].fps == 24.1


async def test_encode_falls_back_to_the_legacy_text_progress_format(tmp_path: Path) -> None:
    lines = [
        b"Encoding: task 1 of 1, 34.20 % (55.55 fps, avg 48.22 fps, ETA 00h12m03s)\n",
        b"Encoding: task 1 of 1, 70.00 % (60.00 fps, avg 50.00 fps, ETA 00h05m00s)\n",
    ]
    process = _FakeStreamingProcess(lines)
    adapter = HandBrakeAdapter(create_subprocess=_streaming_factory(process))

    events: list[EncodeProgress] = []

    async def on_progress(progress: EncodeProgress) -> None:
        events.append(progress)

    await adapter.encode(
        tmp_path / "src.mkv",
        tmp_path / "dst.mkv",
        preset_file=tmp_path / "preset.json",
        preset_name="Spindle 1080p H.265",
        on_progress=on_progress,
    )

    assert [round(e.progress, 4) for e in events] == [0.342, 0.70]
    assert events[0].fps == 55.55
    assert events[0].eta_seconds == 12 * 60 + 3
    assert events[1].eta_seconds == 5 * 60


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(1, "cancelled"), (2, "invalid input"), (3, "initialize"), (7, "exited with 7")],
)
async def test_encode_maps_exit_codes_to_distinct_messages(
    tmp_path: Path, exit_code: int, expected: str
) -> None:
    process = _FakeStreamingProcess([], returncode=exit_code)
    adapter = HandBrakeAdapter(create_subprocess=_streaming_factory(process))

    with pytest.raises(HandBrakeError, match=expected):
        await adapter.encode(
            tmp_path / "src.mkv",
            tmp_path / "dst.mkv",
            preset_file=tmp_path / "preset.json",
            preset_name="Spindle 1080p H.265",
        )


async def test_cancel_terminates_the_registered_encode(tmp_path: Path) -> None:
    process = _FakeStreamingProcess([], auto_close=False)
    adapter = HandBrakeAdapter(create_subprocess=_streaming_factory(process))

    encode_task = asyncio.create_task(
        adapter.encode(
            tmp_path / "src.mkv",
            tmp_path / "dst.mkv",
            preset_file=tmp_path / "preset.json",
            preset_name="Spindle 1080p H.265",
            handle=99,
        )
    )
    for _ in range(200):
        if adapter._processes.get(99) is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("encode() never registered its process under handle 99")

    await adapter.cancel(99)

    with pytest.raises(HandBrakeError):
        await encode_task
    assert process.terminate_calls == 1


async def test_cancel_is_a_noop_for_an_unknown_handle() -> None:
    process = _FakeStreamingProcess([])
    adapter = HandBrakeAdapter(create_subprocess=_streaming_factory(process))

    await adapter.cancel(12345)  # must not raise

    assert process.terminate_calls == 0
