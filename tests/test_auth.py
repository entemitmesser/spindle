"""Phase 9 task 1: single-user password auth. Uses a module-local `app`
fixture (same shape as `conftest.py`'s, plus `SPINDLE_AUTH_ENABLED=true`) so
every other test module keeps running with auth off, per its own default.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from spindle.main import create_app
from spindle.services.auth import AuthService


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SPINDLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPINDLE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SPINDLE_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SPINDLE_DEV_MODE", "true")
    monkeypatch.setenv("SPINDLE_RUN_MIGRATIONS", "false")
    monkeypatch.setenv("SPINDLE_RUN_BACKGROUND_WORKERS", "false")
    monkeypatch.setenv("SPINDLE_AUTH_ENABLED", "true")
    yield create_app()


async def test_unauthenticated_api_call_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/jobs")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


async def test_health_and_auth_routes_stay_reachable_without_a_session(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/api/v1/system/health")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_me_reports_needs_setup_before_a_password_exists(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/auth/me")).json()
    assert body == {"auth_enabled": True, "needs_setup": True, "authenticated": False}


async def test_setup_then_authenticated_requests_succeed(client: httpx.AsyncClient) -> None:
    setup = await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    assert setup.status_code == 200
    assert setup.json()["authenticated"] is True

    # the setup response's Set-Cookie is now on the client -- no separate login needed
    assert (await client.get("/api/v1/jobs")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["authenticated"] is True


async def test_setup_twice_conflicts(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    second = await client.post("/api/v1/auth/setup", json={"password": "another password entirely"})
    assert second.status_code == 409


async def test_login_with_wrong_password_is_rejected(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    client.cookies.clear()

    response = await client.post("/api/v1/auth/login", json={"password": "wrong"})
    assert response.status_code == 401
    assert (await client.get("/api/v1/jobs")).status_code == 401


async def test_login_with_correct_password_succeeds(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    client.cookies.clear()

    response = await client.post("/api/v1/auth/login", json={"password": "correct horse battery"})
    assert response.status_code == 200
    assert (await client.get("/api/v1/jobs")).status_code == 200


async def test_logout_invalidates_the_session(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    assert (await client.get("/api/v1/jobs")).status_code == 200

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert (await client.get("/api/v1/jobs")).status_code == 401


async def test_login_is_rate_limited_after_five_bad_attempts(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/auth/setup", json={"password": "correct horse battery"})
    client.cookies.clear()

    for _ in range(5):
        response = await client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert response.status_code == 401

    limited = await client.post("/api/v1/auth/login", json={"password": "wrong"})
    assert limited.status_code == 429

    # even the *correct* password is refused once rate-limited
    still_limited = await client.post(
        "/api/v1/auth/login", json={"password": "correct horse battery"}
    )
    assert still_limited.status_code == 429


async def test_session_cookie_signature_is_verified(running_app: FastAPI) -> None:
    auth: AuthService = running_app.state.auth
    real = auth.create_session_cookie()
    assert auth.verify_session_cookie(real) is True
    tampered = real.rsplit(".", 1)[0] + ".not-a-real-signature"
    assert auth.verify_session_cookie(tampered) is False
    assert auth.verify_session_cookie("garbage") is False
