from __future__ import annotations

import httpx
from fastapi import FastAPI


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


async def test_unknown_api_route_returns_json_404(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "message" in body
