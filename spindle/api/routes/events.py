"""`/api/v1/events`, per architecture.md §4/§5 and phase-07-api-realtime.md
task 3.

`GET /events/stream` streams straight from `EventBus.subscribe` — never from
inside a DB session (the phase doc's gotcha: a stalled generator holding a
SQLite read transaction open for hours). Each `Event` row handed to us is
already detached-but-populated (see `EventBus.emit`'s `refresh()` before its
`async with` block exits), so no session is needed here at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlmodel import col

from spindle.models.event import Event
from spindle.schemas.common import Page
from spindle.schemas.events import EventOut
from spindle.services.events import EventBus

router = APIRouter()

_HEARTBEAT_SECONDS = 20.0


def _format_event(event: Event) -> str:
    payload = {
        "type": event.type,
        "ts": event.ts.isoformat(),
        "job_id": event.job_id,
        "drive_id": event.drive_id,
        "data": event.data,
    }
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(payload)}\n\n"


async def _sse_generator(event_bus: EventBus, last_event_id: int | None) -> AsyncIterator[str]:
    """`asyncio.wait_for(anext(subscriber), ...)` would be the obvious way to
    write this, but a `wait_for` timeout *cancels* the awaited coroutine —
    which tears down `subscriber` (an async generator, not a disposable
    per-call awaitable) via its `finally` block, unsubscribing it from
    `EventBus` for good. The next loop iteration's `anext()` then just raises
    `StopAsyncIteration` and the stream silently dies at the first idle
    heartbeat. Keeping one persistent `anext()` task alive across timeouts
    (only ever cancelled in this generator's own `finally`, on real
    shutdown) avoids that."""
    subscriber = event_bus.subscribe(last_event_id)
    pending = asyncio.ensure_future(anext(subscriber))
    try:
        while True:
            done, _pending = await asyncio.wait({pending}, timeout=_HEARTBEAT_SECONDS)
            if pending not in done:
                yield ": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = asyncio.ensure_future(anext(subscriber))
            yield _format_event(event)
    finally:
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pending
        with contextlib.suppress(Exception):
            await subscriber.aclose()


@router.get("/stream", operation_id="stream_events")
async def stream_events(request: Request) -> StreamingResponse:
    event_bus: EventBus = request.app.state.event_bus
    header = request.headers.get("last-event-id")
    last_event_id = int(header) if header and header.isdigit() else None

    return StreamingResponse(
        _sse_generator(event_bus, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx: required or it buffers the stream and heartbeats never
            # reach the client (phase doc gotcha).
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", operation_id="list_events")
async def list_events(
    request: Request, job_id: int | None = None, limit: int = 50
) -> Page[EventOut]:
    limit = max(1, min(limit, 200))
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        stmt = select(Event)
        if job_id is not None:
            stmt = stmt.where(col(Event.job_id) == job_id)
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        result = await session.execute(stmt.order_by(col(Event.id).desc()).limit(limit))
        rows = list(result.scalars().all())
    return Page[EventOut](
        items=[EventOut.from_model(r) for r in rows], total=total, limit=limit, offset=0
    )
