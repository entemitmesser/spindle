from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from spindle.adapters.ffmpeg import FfmpegAdapter

_PROBE_PAYLOAD = {
    "format": {"duration": "7199.958000"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "ac3"},
    ],
}

_PROBE_PAYLOAD_WITH_SUBTITLES = {
    "format": {"duration": "7199.958000"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "ac3"},
        {
            "index": 2,
            "codec_type": "subtitle",
            "codec_name": "hdmv_pgs_subtitle",
            "tags": {"language": "deu"},
            "disposition": {"forced": 1, "default": 0},
        },
        {
            "index": 3,
            "codec_type": "subtitle",
            "codec_name": "hdmv_pgs_subtitle",
            "tags": {"language": "eng"},
            "disposition": {"forced": 0, "default": 1},
        },
    ],
}


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _factory(process: _FakeProcess) -> Callable[..., Awaitable[_FakeProcess]]:
    async def _create(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    return _create


async def test_probe_parses_duration_and_video_stream(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"not really a video, but ffprobe is faked")
    process = _FakeProcess(returncode=0, stdout=json.dumps(_PROBE_PAYLOAD).encode())
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(path)

    assert probe is not None
    assert probe.duration_seconds == 7199.958
    assert probe.has_video is True
    assert probe.video_codec == "hevc"
    assert probe.width == 1920
    assert probe.height == 1080
    assert probe.size_bytes == path.stat().st_size
    assert probe.subtitle_streams == ()


async def test_probe_parses_subtitle_streams_with_forced_and_language(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"not really a video, but ffprobe is faked")
    process = _FakeProcess(returncode=0, stdout=json.dumps(_PROBE_PAYLOAD_WITH_SUBTITLES).encode())
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(path)

    assert probe is not None
    assert len(probe.subtitle_streams) == 2
    forced, default = probe.subtitle_streams
    assert forced.language == "deu"
    assert forced.is_forced is True
    assert forced.is_default is False
    assert forced.codec == "hdmv_pgs_subtitle"
    assert default.language == "eng"
    assert default.is_forced is False
    assert default.is_default is True


async def test_probe_returns_none_for_missing_file(tmp_path: Path) -> None:
    process = _FakeProcess(returncode=0, stdout=b"{}")
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(tmp_path / "missing.mkv")

    assert probe is None


async def test_probe_returns_none_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.mkv"
    path.write_bytes(b"")
    process = _FakeProcess(returncode=0, stdout=b"{}")
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(path)

    assert probe is None


async def test_probe_returns_none_when_ffprobe_fails(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.mkv"
    path.write_bytes(b"garbage")
    process = _FakeProcess(returncode=1, stdout=b"")
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(path)

    assert probe is None


async def test_probe_returns_none_on_unparseable_json(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"data")
    process = _FakeProcess(returncode=0, stdout=b"not json")
    adapter = FfmpegAdapter(create_subprocess=_factory(process))

    probe = await adapter.probe(path)

    assert probe is None
