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


async def test_list_presets_includes_builtins(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/settings/presets")

    assert response.status_code == 200
    presets = response.json()
    assert any(p["builtin"] for p in presets)


async def test_test_settings_target_tmdb_not_configured(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/settings/test/tmdb")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "latency_ms" in body


async def test_test_settings_target_jellyfin_uses_adapter(
    client: httpx.AsyncClient, running_app
) -> None:
    async def fake_ping() -> bool:
        return True

    running_app.state.jellyfin._base_url = "http://jellyfin.local"
    running_app.state.jellyfin._token = "token"  # noqa: S105 -- test fixture, not a real secret
    running_app.state.jellyfin.ping = fake_ping

    response = await client.post("/api/v1/settings/test/jellyfin")

    assert response.status_code == 200
    assert response.json()["ok"] is True


async def test_test_settings_target_notify_requires_webhook(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/settings/test/notify")

    assert response.status_code == 200
    assert response.json()["ok"] is False


async def test_test_settings_target_unknown_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/settings/test/bogus")

    assert response.status_code == 422
