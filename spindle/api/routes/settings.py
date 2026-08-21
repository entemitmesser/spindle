from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Request

from spindle.config import ConfigStore
from spindle.errors import AdapterError
from spindle.presets import list_presets
from spindle.schemas.settings import PresetOut, SettingsResponse, SettingsTestResult

router = APIRouter()


@router.get("", operation_id="get_settings")
async def get_settings_endpoint(request: Request) -> SettingsResponse:
    store: ConfigStore = request.app.state.config_store
    return await store.get()


@router.put("", operation_id="update_settings")
async def update_settings_endpoint(request: Request, partial: dict[str, Any]) -> SettingsResponse:
    store: ConfigStore = request.app.state.config_store
    return await store.update(partial)


@router.get("/presets", operation_id="list_presets")
async def list_presets_endpoint(request: Request) -> list[PresetOut]:
    data_dir = request.app.state.settings.data_dir
    presets = list_presets(data_dir / "presets")
    return [PresetOut(name=p.name, builtin=p.builtin) for p in presets]


@router.post("/test/{target}", operation_id="test_settings_target")
async def test_settings_target(
    request: Request, target: Literal["tmdb", "opensubtitles", "jellyfin", "notify"]
) -> SettingsTestResult:
    start = time.monotonic()
    ok, message = await _run_test(request, target)
    latency_ms = int((time.monotonic() - start) * 1000)
    return SettingsTestResult(ok=ok, message=message, latency_ms=latency_ms)


async def _run_test(request: Request, target: str) -> tuple[bool, str]:
    if target == "tmdb":
        tmdb = request.app.state.tmdb
        if not tmdb.is_configured:
            return False, "No TMDB API key configured."
        try:
            await tmdb.search_movie("test")
        except AdapterError as exc:
            return False, str(exc)
        return True, "TMDB reachable."

    if target == "opensubtitles":
        opensubtitles = request.app.state.opensubtitles
        if not opensubtitles.is_configured:
            return False, "No OpenSubtitles API key configured."
        try:
            await opensubtitles.search(languages=["en"], query="test")
        except AdapterError as exc:
            return False, str(exc)
        return True, "OpenSubtitles reachable."

    if target == "jellyfin":
        jellyfin = request.app.state.jellyfin
        if not jellyfin.is_configured:
            return False, "Jellyfin URL/token not configured."
        ok = await jellyfin.ping()
        return ok, "Jellyfin reachable." if ok else "Could not reach Jellyfin."

    notifier = request.app.state.notifier
    config = await request.app.state.config_store.get()
    if not config.notifications.webhook_url:
        return False, "No webhook URL configured."
    ok = await notifier.send_test(config.notifications.webhook_url)
    return ok, "Webhook reachable." if ok else "Could not reach the webhook URL."
