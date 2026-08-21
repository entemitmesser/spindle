from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from spindle.config import NotificationConfig
from spindle.models.job import Job
from spindle.services.notifier import NotificationService


def _service(handler: Callable[[httpx.Request], httpx.Response]) -> NotificationService:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return NotificationService(client)


def _job(**overrides: object) -> Job:
    defaults: dict[str, object] = {"id": 1, "title": "Amelie", "year": 2001}
    return Job(**{**defaults, **overrides})  # type: ignore[arg-type]


async def test_notify_job_sends_a_webhook_when_enabled_and_event_is_wanted() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        return httpx.Response(200)

    service = _service(handler)
    config = NotificationConfig(enabled=True, webhook_url="http://hooks.example/x")  # type: ignore[call-arg]

    sent = await service.notify_job(_job(), event="completed", config=config)

    assert sent is True
    assert len(calls) == 1


async def test_notify_job_does_nothing_when_disabled() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    service = _service(handler)
    config = NotificationConfig(enabled=False, webhook_url="http://hooks.example/x")  # type: ignore[call-arg]

    sent = await service.notify_job(_job(), event="completed", config=config)

    assert sent is False
    assert calls == 0


async def test_notify_job_does_nothing_without_a_webhook_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    service = _service(handler)
    config = NotificationConfig(enabled=True, webhook_url=None)  # type: ignore[call-arg]

    assert await service.notify_job(_job(), event="completed", config=config) is False


async def test_notify_job_skips_an_event_not_in_notify_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    service = _service(handler)
    config = NotificationConfig(  # type: ignore[call-arg]
        enabled=True, webhook_url="http://hooks.example/x", notify_on=["failed"]
    )

    assert await service.notify_job(_job(), event="completed", config=config) is False


async def test_notify_job_returns_false_and_swallows_a_webhook_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    service = _service(handler)
    config = NotificationConfig(enabled=True, webhook_url="http://hooks.example/x")  # type: ignore[call-arg]

    assert await service.notify_job(_job(), event="completed", config=config) is False


async def test_notify_job_payload_includes_title_year_duration_and_output_path() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(200)

    service = _service(handler)
    config = NotificationConfig(enabled=True, webhook_url="http://hooks.example/x")  # type: ignore[call-arg]

    await service.notify_job(
        _job(),
        event="completed",
        config=config,
        duration_seconds=125,
        output_path="/library/Movies/Amelie (2001)/Amelie (2001).mkv",
    )

    assert "Amelie (2001)" in captured["message"]
    assert "2 min" in captured["message"]
    assert "/library/Movies/Amelie (2001)" in captured["message"]


async def test_notify_warning_sends_when_system_warning_is_wanted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    service = _service(handler)
    config = NotificationConfig(  # type: ignore[call-arg]
        enabled=True, webhook_url="http://hooks.example/x", notify_on=["system_warning"]
    )

    sent = await service.notify_warning(code="low_disk_space", message="disk low", config=config)

    assert sent is True
    assert calls == 1
