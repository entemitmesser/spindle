from __future__ import annotations

import httpx


async def test_get_settings_returns_defaults(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["rip"]["backend"] == "makemkv"
    assert body["behaviour"]["auto_eject"] is True


async def test_put_settings_updates_only_given_keys(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings", json={"rip": {"backend": "handbrake"}})

    assert response.status_code == 200
    body = response.json()
    assert body["rip"]["backend"] == "handbrake"
    assert body["rip"]["title_selection"] == "longest"
    assert body["transcode"]["workers"] == 1

    follow_up = await client.get("/api/v1/settings")
    assert follow_up.json()["rip"]["backend"] == "handbrake"


async def test_put_settings_rejects_invalid_enum_value(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings", json={"rip": {"backend": "nope"}})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
