from __future__ import annotations

import asyncio
import contextlib

from spindle.services.events import EventBus, EventIn


async def test_late_subscriber_replays_events_after_last_seen_id(event_bus: EventBus) -> None:
    await event_bus.emit(EventIn(type="job.created", data={"n": 1}))
    checkpoint = await event_bus.emit(EventIn(type="job.created", data={"n": 2}))
    third = await event_bus.emit(EventIn(type="job.created", data={"n": 3}))

    subscriber = event_bus.subscribe(last_event_id=checkpoint.id)
    try:
        replayed = await asyncio.wait_for(anext(subscriber), timeout=1)
        assert replayed.id == third.id
        assert replayed.data == {"n": 3}
    finally:
        await subscriber.aclose()


async def test_subscriber_receives_live_events_emitted_after_it_connects(
    event_bus: EventBus,
) -> None:
    checkpoint = await event_bus.emit(EventIn(type="job.created", data={"n": 1}))

    subscriber = event_bus.subscribe(last_event_id=checkpoint.id)
    try:
        pending = asyncio.ensure_future(anext(subscriber))
        await asyncio.sleep(0.05)  # let the generator register its queue before we emit

        live = await event_bus.emit(EventIn(type="job.created", data={"n": 2}))
        received = await asyncio.wait_for(pending, timeout=1)
        assert received.id == live.id
    finally:
        await subscriber.aclose()


async def test_connect_disconnect_cycles_leave_no_subscribers_behind(event_bus: EventBus) -> None:
    """Phase-07 SSE acceptance criterion: 100 connect/disconnect cycles must
    not leak subscriber queues."""
    for _ in range(100):
        subscriber = event_bus.subscribe()
        pending = asyncio.ensure_future(anext(subscriber))
        await asyncio.sleep(0)
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        await subscriber.aclose()

    assert event_bus._subscribers == {}  # noqa: SLF001 -- whitebox leak check


async def test_producer_never_blocks_on_a_stalled_subscriber(event_bus: EventBus) -> None:
    """A subscriber that stops reading must never make emit() hang."""
    subscriber = event_bus.subscribe()
    registered = asyncio.ensure_future(anext(subscriber))
    await asyncio.sleep(0.05)  # let the generator register its queue

    async def emit_burst() -> None:
        # Comfortably larger than the subscriber queue's maxsize (500).
        for i in range(600):
            await event_bus.emit(EventIn(type="task.progress", data={"i": i}))

    await asyncio.wait_for(emit_burst(), timeout=5)

    registered.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await registered
    await subscriber.aclose()
