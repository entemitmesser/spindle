from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.models.event import Event

logger = structlog.get_logger(__name__)

_PROGRESS_EVENT_TYPES: Final = {"task.progress"}
_QUEUE_MAXSIZE: Final = 500
_RETENTION_LIMIT: Final = 50_000
_DEFAULT_REPLAY: Final = 50
_CLOSED: Final = object()

_QueueItem = Event | object


@dataclass(frozen=True)
class EventIn:
    type: str
    data: dict[str, object] = field(default_factory=dict)
    job_id: int | None = None
    drive_id: int | None = None


class EventBus:
    """In-process pub/sub over the `event` table.

    `emit` persists then fans out to subscribers; it never blocks on a slow
    subscriber. A full queue first drops its oldest `task.progress` entry to
    make room, and only disconnects the subscriber (after a `stream.overflow`
    notice) if that isn't enough.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._subscribers: dict[int, asyncio.Queue[_QueueItem]] = {}
        self._next_subscriber_id = 0
        self._lock = asyncio.Lock()

    async def emit(self, event_in: EventIn) -> Event:
        row = Event(
            type=event_in.type,
            job_id=event_in.job_id,
            drive_id=event_in.drive_id,
            data=dict(event_in.data),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)

        await self._fan_out(row)
        return row

    async def subscribe(self, last_event_id: int | None = None) -> AsyncGenerator[Event, None]:
        """Replays history before going live: everything after `last_event_id`
        if given (SSE reconnect via `Last-Event-ID`), otherwise the last
        `_DEFAULT_REPLAY` events (a fresh connect) — phase-07 task 3."""
        async with self._session_factory() as session:
            if last_event_id is not None:
                result = await session.execute(
                    select(Event).where(col(Event.id) > last_event_id).order_by(col(Event.id))
                )
                replay = list(result.scalars().all())
            else:
                result = await session.execute(
                    select(Event).order_by(col(Event.id).desc()).limit(_DEFAULT_REPLAY)
                )
                replay = list(reversed(result.scalars().all()))
        for row in replay:
            yield row

        queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = queue

        try:
            while True:
                item = await queue.get()
                if item is _CLOSED:
                    return
                assert isinstance(item, Event)  # noqa: S101 -- narrows _QueueItem for mypy
                yield item
        finally:
            async with self._lock:
                self._subscribers.pop(subscriber_id, None)

    async def trim_retention(self) -> None:
        async with self._session_factory() as session:
            cutoff = await session.execute(
                select(col(Event.id))
                .order_by(col(Event.id).desc())
                .offset(_RETENTION_LIMIT)
                .limit(1)
            )
            cutoff_id = cutoff.scalar_one_or_none()
            if cutoff_id is None:
                return
            await session.execute(delete(Event).where(col(Event.id) <= cutoff_id))
            await session.commit()

    async def run_retention_loop(self, *, interval_seconds: float = 3600.0) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.trim_retention()
            except Exception:  # noqa: BLE001 -- retention must never kill the loop
                logger.exception("events.retention_failed")

    async def _fan_out(self, row: Event) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.items())

        for subscriber_id, queue in subscribers:
            if queue.full() and not self._make_room(queue):
                await self._disconnect(subscriber_id, queue)
                continue
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull:
                await self._disconnect(subscriber_id, queue)

    @staticmethod
    def _make_room(queue: asyncio.Queue[_QueueItem]) -> bool:
        """Drop the oldest `task.progress` item from `queue`. Returns whether one was found."""
        items: list[_QueueItem] = []
        dropped = False
        while not queue.empty():
            item = queue.get_nowait()
            if not dropped and isinstance(item, Event) and item.type in _PROGRESS_EVENT_TYPES:
                dropped = True
                continue
            items.append(item)
        for item in items:
            queue.put_nowait(item)
        return dropped

    async def _disconnect(self, subscriber_id: int, queue: asyncio.Queue[_QueueItem]) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)
        # Make room for both the overflow notice and the close sentinel below.
        while queue.maxsize - queue.qsize() < 2:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(Event(type="stream.overflow", data={}))
        queue.put_nowait(_CLOSED)
