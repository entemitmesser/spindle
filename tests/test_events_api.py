from __future__ import annotations

import asyncio

import httpx

from spindle.api.routes.events import _sse_generator
from spindle.services.events import EventIn
from tests.factories import make_job

# httpx's `ASGITransport` collects the *entire* ASGI response body before
# handing anything back to the caller (see `ASGIResponseStream.__aiter__` —
# it joins every chunk into one), so `client.stream()` against an in-process
# ASGI app can't observe an intentionally-infinite SSE stream incrementally.
# These tests drive `_sse_generator` directly instead — the same code the
# `/events/stream` route wraps in a `StreamingResponse` — which exercises
# ordering/replay/heartbeat-safety without that transport limitation.


async def test_sse_generator_yields_events_in_order_with_ids(running_app) -> None:
    event_bus = running_app.state.event_bus
    generator = _sse_generator(event_bus, None)

    async def emit_soon() -> None:
        await asyncio.sleep(0.02)
        for i in range(3):
            await event_bus.emit(EventIn(type="job.created", data={"n": i}))

    emit_task = asyncio.create_task(emit_soon())
    chunks = []
    async for chunk in generator:
        chunks.append(chunk)
        if len(chunks) == 3:
            break
    await emit_task
    await generator.aclose()

    ids = [int(c.splitlines()[0].removeprefix("id: ")) for c in chunks]
    assert ids == sorted(ids)
    for i, chunk in enumerate(chunks):
        assert f'"n": {i}' in chunk
        assert chunk.startswith(f"id: {ids[i]}\nevent: job.created\n")


async def test_sse_generator_reconnect_replays_only_missed_events(running_app) -> None:
    event_bus = running_app.state.event_bus
    first = await event_bus.emit(EventIn(type="job.created", data={"n": 1}))
    second = await event_bus.emit(EventIn(type="job.created", data={"n": 2}))

    generator = _sse_generator(event_bus, first.id)
    chunk = await anext(generator)
    await generator.aclose()

    assert f"id: {second.id}" in chunk
    assert '"n": 2' in chunk
    assert 'n": 1' not in chunk


async def test_sse_generator_survives_an_idle_heartbeat(running_app, monkeypatch) -> None:
    """The regression this guards against: a naive `asyncio.wait_for(anext(...))`
    heartbeat implementation cancels (and thereby closes) the subscriber on
    every idle timeout, silently ending the stream after the first ping."""
    import spindle.api.routes.events as events_module

    monkeypatch.setattr(events_module, "_HEARTBEAT_SECONDS", 0.02)
    event_bus = running_app.state.event_bus
    generator = _sse_generator(event_bus, None)

    ping = await anext(generator)
    assert ping == ": ping\n\n"

    await event_bus.emit(EventIn(type="job.created", data={"n": 1}))
    chunk = await asyncio.wait_for(anext(generator), timeout=1)
    await generator.aclose()

    assert '"n": 1' in chunk


async def test_list_events_filters_by_job_id(
    client: httpx.AsyncClient, running_app, session
) -> None:
    job = await make_job(session)
    event_bus = running_app.state.event_bus
    await event_bus.emit(EventIn(type="job.created", job_id=job.id, data={}))
    await event_bus.emit(EventIn(type="job.created", job_id=None, data={}))

    response = await client.get("/api/v1/events", params={"job_id": job.id})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["job_id"] == job.id


class _FakeApp:
    def __init__(self, state: object) -> None:
        self.state = state


class _FakeRequest:
    """`stream_events(request)` only reads `request.app.state` and
    `request.headers` — a real `Request` would work too, but building one
    means going through ASGI, which is exactly what these tests avoid (see
    module docstring)."""

    def __init__(self, app_state: object, headers: dict[str, str] | None = None) -> None:
        self.app = _FakeApp(app_state)
        self.headers = headers or {}


async def test_stream_events_route_returns_sse_headers(running_app) -> None:
    from spindle.api.routes.events import stream_events

    response = await stream_events(_FakeRequest(running_app.state))  # type: ignore[arg-type]

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    await response.body_iterator.aclose()
