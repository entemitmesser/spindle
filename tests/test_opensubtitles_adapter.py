from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from spindle.adapters.opensubtitles import (
    OpenSubtitlesAdapter,
    OpenSubtitlesError,
    OpenSubtitlesQuotaExceededError,
)


def _adapter(handler, *, api_key: str | None = "test-key") -> OpenSubtitlesAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return OpenSubtitlesAdapter(client, api_key=api_key)


async def test_search_prefers_moviehash_hit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/subtitles"
        assert request.url.params["moviehash"] == "8e245d9679d31e12"
        assert request.headers["Api-Key"] == "test-key"
        assert "Spindle/" in request.headers["User-Agent"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "attributes": {
                            "release": "Movie.2020.1080p",
                            "download_count": 42,
                            "moviehash_match": True,
                            "files": [{"file_id": 111}],
                        }
                    }
                ]
            },
        )

    adapter = _adapter(handler)
    results = await adapter.search(languages=["en"], moviehash="8e245d9679d31e12", tmdb_id=27205)

    assert len(results) == 1
    assert results[0]["attributes"]["files"][0]["file_id"] == 111


async def test_search_falls_back_to_query_when_no_hash_or_tmdb_id() -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json={"data": []})

    adapter = _adapter(handler)
    await adapter.search(languages=["en"], query="Inception")

    assert seen_params["query"] == "Inception"
    assert "moviehash" not in seen_params
    assert "tmdb_id" not in seen_params


async def test_search_returns_empty_without_any_criteria_and_makes_no_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    adapter = _adapter(handler)
    results = await adapter.search(languages=["en"])

    assert results == []
    assert calls == 0


async def test_search_zero_results_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "total_count": 0})

    adapter = _adapter(handler)
    results = await adapter.search(languages=["en"], query="A Very Obscure Title")

    assert results == []


async def test_download_returns_file_bytes_via_the_short_lived_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/download":
            return httpx.Response(
                200, json={"link": "https://dl.opensubtitles.com/x", "remaining": 4}
            )
        assert str(request.url) == "https://dl.opensubtitles.com/x"
        return httpx.Response(200, content=b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    adapter = _adapter(handler)
    data = await adapter.download(111)

    assert data == b"1\n00:00:01,000 --> 00:00:02,000\nhi\n"


async def test_download_quota_exhausted_raises_quota_error_not_generic_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(406, json={"message": "Your quota is exceeded."})

    adapter = _adapter(handler)
    with pytest.raises(OpenSubtitlesQuotaExceededError):
        await adapter.download(111)


async def test_search_rate_limited_raises_quota_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"message": "Too many requests"}, headers={"retry-after": "5"}
        )

    adapter = _adapter(handler)
    with pytest.raises(OpenSubtitlesQuotaExceededError):
        await adapter.search(languages=["en"], query="Inception")


async def test_no_api_key_returns_empty_without_a_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    adapter = _adapter(handler, api_key=None)
    assert await adapter.search(languages=["en"], query="Inception") == []
    assert calls == 0


async def test_download_without_api_key_raises() -> None:
    adapter = _adapter(lambda r: httpx.Response(200, json={}), api_key=None)
    with pytest.raises(OpenSubtitlesError):
        await adapter.download(1)


async def test_other_http_errors_raise_generic_adapter_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    adapter = _adapter(handler)
    with pytest.raises(OpenSubtitlesError):
        await adapter.search(languages=["en"], query="Inception")
