from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pytest

from spindle.adapters.handbrake import HandBrakeAdapter, HandBrakeError

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
