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


async def test_system_info_has_empty_placeholders(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system")

    assert response.status_code == 200
    body = response.json()
    assert body["encoders"] == []
    assert body["backends"] == []
    assert body["disk_space"] == []


async def test_unknown_api_route_returns_json_404(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "message" in body
