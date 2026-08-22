from __future__ import annotations

import httpx
from fastapi import FastAPI

from spindle.config import ConfigStore
from spindle.services.events import EventBus, EventIn


async def test_health_returns_ok(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert body["uptime_seconds"] >= 0


async def test_system_info_has_empty_placeholders_before_lifespan(app: FastAPI) -> None:
    """Without the lifespan's adapters on `app.state`, the route degrades to
    empty backends/warnings instead of a 500 (`hasattr` guards in the
    handler) — disk usage still works since it only needs `settings`, and
    `work`/`data` always exist (`Settings._ensure_dirs_writable`)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system")

    assert response.status_code == 200
    body = response.json()
    assert body["encoders"] == []
    assert body["backends"] == {}
    assert body["warnings"] == []
    assert {"work", "data"} <= {entry["label"] for entry in body["disk"]}


async def test_system_info_reports_real_backend_status(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/system")

    assert response.status_code == 200
    body = response.json()
    assert set(body["backends"]) == {"makemkv", "handbrake", "mkvpropedit"}
    for status in body["backends"].values():
        assert isinstance(status["available"], bool)
    assert {"work", "data"} <= {entry["label"] for entry in body["disk"]}


async def test_system_info_drops_stale_opensubtitles_warning_once_disabled(
    client: httpx.AsyncClient,
    event_bus: EventBus,
    config_store: ConfigStore,
) -> None:
    """`opensubtitles_not_configured` is only ever emitted once, at startup
    (`run_startup_checks`). If the user later disables subtitles in settings,
    that past event must not keep haunting the dashboard forever — `GET
    /system` should re-validate it against the *current* config on every
    call, same as it already does for disk space."""
    await event_bus.emit(
        EventIn(
            type="system.warning",
            data={
                "code": "opensubtitles_not_configured",
                "message": "Subtitles are enabled with OpenSubtitles as the "
                "provider, but no API key is configured.",
            },
        )
    )

    response = await client.get("/api/v1/system")
    assert response.status_code == 200
    codes = {w["code"] for w in response.json()["warnings"]}
    assert "opensubtitles_not_configured" in codes

    await config_store.update({"subtitles": {"enabled": False}})

    response = await client.get("/api/v1/system")
    assert response.status_code == 200
    codes = {w["code"] for w in response.json()["warnings"]}
    assert "opensubtitles_not_configured" not in codes


async def test_unknown_api_route_returns_json_404(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "message" in body
