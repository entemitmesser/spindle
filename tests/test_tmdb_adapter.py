from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from spindle.adapters.tmdb import TmdbAdapter
from spindle.errors import AdapterError


def _adapter(tmp_path: Path, handler, *, api_key: str | None = "test-key") -> TmdbAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return TmdbAdapter(client, api_key=api_key, cache_dir=tmp_path / "cache" / "tmdb")


async def test_no_api_key_returns_empty_without_a_request(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": []})

    adapter = _adapter(tmp_path, handler, api_key=None)
    assert await adapter.search_movie("Inception") == []
    assert calls == 0


async def test_search_movie_returns_results(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/search/movie"
        assert request.url.params["query"] == "Inception"
        return httpx.Response(200, json={"results": [{"id": 27205, "title": "Inception"}]})

    adapter = _adapter(tmp_path, handler)
    results = await adapter.search_movie("Inception")
    assert results == [{"id": 27205, "title": "Inception"}]


async def test_second_call_is_served_from_cache(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"id": 1}]})

    adapter = _adapter(tmp_path, handler)
    await adapter.search_movie("Amelie", year=2001)
    await adapter.search_movie("Amelie", year=2001)
    assert calls == 1


async def test_different_params_are_not_conflated_in_cache(tmp_path: Path) -> None:
    seen_years: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_years.append(int(request.url.params["year"]) if "year" in request.url.params else None)
        return httpx.Response(200, json={"results": []})

    adapter = _adapter(tmp_path, handler)
    await adapter.search_movie("Amelie", year=2001)
    await adapter.search_movie("Amelie", year=2002)
    assert seen_years == [2001, 2002]


async def test_expired_cache_entry_triggers_refetch(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"id": calls}]})

    adapter = _adapter(tmp_path, handler)
    await adapter.search_movie("Amelie")

    cache_files = list((tmp_path / "cache" / "tmdb").glob("*.json"))
    assert len(cache_files) == 1
    envelope = json.loads(cache_files[0].read_text(encoding="utf-8"))
    envelope["cached_at"] = 0  # far in the past
    cache_files[0].write_text(json.dumps(envelope), encoding="utf-8")

    results = await adapter.search_movie("Amelie")
    assert calls == 2
    assert results == [{"id": 2}]


async def test_http_error_raises_adapter_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    adapter = _adapter(tmp_path, handler)
    with pytest.raises(AdapterError):
        await adapter.search_movie("Amelie")


async def test_download_image_writes_bytes_to_dest(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://image.tmdb.org/t/p/original/poster.jpg"
        return httpx.Response(200, content=b"fake-jpeg-bytes")

    adapter = _adapter(tmp_path, handler)
    dest = tmp_path / "artwork" / "poster.jpg"

    ok = await adapter.download_image("/poster.jpg", dest)

    assert ok is True
    assert dest.read_bytes() == b"fake-jpeg-bytes"


async def test_download_image_returns_false_on_http_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    adapter = _adapter(tmp_path, handler)
    dest = tmp_path / "poster.jpg"

    ok = await adapter.download_image("/missing.jpg", dest)

    assert ok is False
    assert not dest.exists()


async def test_download_image_returns_false_for_empty_path(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x")

    adapter = _adapter(tmp_path, handler)
    assert await adapter.download_image("", tmp_path / "poster.jpg") is False
    assert calls == 0


async def test_details_and_external_ids_hit_expected_paths(tmp_path: Path) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    adapter = _adapter(tmp_path, handler)
    await adapter.details(27205, media_kind="movie")
    await adapter.external_ids(27205, media_kind="tv")
    assert seen_paths == ["/3/movie/27205", "/3/tv/27205/external_ids"]
