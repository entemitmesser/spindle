from __future__ import annotations

from collections.abc import Callable

import httpx

from spindle.adapters.jellyfin import JellyfinAdapter

_TEST_TOKEN = "test-token"  # noqa: S105 -- fixture value for a mock transport, not a real secret


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str | None = "http://jellyfin.local:8096",
    token: str | None = _TEST_TOKEN,
) -> JellyfinAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return JellyfinAdapter(client, base_url=base_url, token=token)


async def test_not_configured_without_base_url_or_token() -> None:
    adapter = _adapter(lambda r: httpx.Response(200), base_url=None, token=None)
    assert adapter.is_configured is False
    assert await adapter.ping() is False
    assert await adapter.refresh_library() is False
    assert await adapter.find_item(1) is None


async def test_ping_true_on_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/System/Info/Public"
        assert request.headers["X-Emby-Token"] == "test-token"
        return httpx.Response(200, json={"ServerName": "jelly"})

    assert await _adapter(handler).ping() is True


async def test_ping_false_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await _adapter(handler).ping() is False


async def test_refresh_library_without_path_hint_refreshes_everything() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.path))
        return httpx.Response(204)

    assert await _adapter(handler).refresh_library() is True
    assert calls == ["/Library/Refresh"]


async def test_refresh_library_resolves_the_containing_virtual_folder() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.path))
        if request.url.path == "/Library/VirtualFolders":
            return httpx.Response(
                200,
                json=[
                    {"ItemId": "abc123", "Locations": ["/library/Movies"]},
                    {"ItemId": "xyz789", "Locations": ["/library/Shows"]},
                ],
            )
        return httpx.Response(204)

    result = await _adapter(handler).refresh_library("/library/Movies/Amelie (2001)")
    assert result is True
    assert calls == ["/Library/VirtualFolders", "/Items/abc123/Refresh"]


async def test_refresh_library_falls_back_to_full_refresh_when_no_folder_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[{"ItemId": "abc", "Locations": ["/other"]}])
        assert request.url.path == "/Library/Refresh"
        return httpx.Response(204)

    assert await _adapter(handler).refresh_library("/library/Movies/Amelie") is True


async def test_refresh_library_false_when_jellyfin_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await _adapter(handler).refresh_library() is False


async def test_find_item_returns_first_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["AnyProviderIdEquals"] == "tmdb.27205"
        return httpx.Response(200, json={"Items": [{"Id": "1", "Name": "Inception"}]})

    item = await _adapter(handler).find_item(27205)
    assert item is not None
    assert item["Name"] == "Inception"


async def test_find_item_retries_then_gives_up_and_returns_none() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"Items": []})

    adapter = _adapter(handler)
    result = await adapter.find_item(27205, retries=3, retry_delay_seconds=0)
    assert result is None
    assert calls == 3
