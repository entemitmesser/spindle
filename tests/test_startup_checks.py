"""Phase 9 task 2: the one-shot startup self-check."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.config import RuntimeConfig
from spindle.models.event import Event
from spindle.services.events import EventBus
from spindle.services.startup_checks import run_startup_checks


class _Unavailable:
    async def is_available(self) -> bool:
        return False


class _Available:
    async def is_available(self) -> bool:
        return True


class _NotConfigured:
    is_configured = False

    async def ping(self) -> bool:
        return False


async def test_warns_when_no_rip_backend_and_library_unwritable(
    event_bus: EventBus, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    unwritable_library = tmp_path / "no-such-parent" / "library"

    await run_startup_checks(
        event_bus=event_bus,
        config=RuntimeConfig(),
        library_dir=unwritable_library,
        makemkv=_Unavailable(),  # type: ignore[arg-type]
        handbrake=_Unavailable(),  # type: ignore[arg-type]
        mkvpropedit=_Available(),  # type: ignore[arg-type]
        opensubtitles=_NotConfigured(),  # type: ignore[arg-type]
        jellyfin=_NotConfigured(),  # type: ignore[arg-type]
    )

    async with session_factory() as s:
        result = await s.execute(select(Event).where(col(Event.type) == "system.warning"))
        codes = {e.data.get("code") for e in result.scalars().all()}

    assert "no_rip_backend" in codes
    assert "opensubtitles_not_configured" in codes
    assert "mkvpropedit_unavailable" not in codes


async def test_quiet_when_everything_is_available_and_configured(
    event_bus: EventBus, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    config = RuntimeConfig(subtitles={"enabled": False})  # type: ignore[arg-type]

    await run_startup_checks(
        event_bus=event_bus,
        config=config,
        library_dir=library_dir,
        makemkv=_Available(),  # type: ignore[arg-type]
        handbrake=_Available(),  # type: ignore[arg-type]
        mkvpropedit=_Available(),  # type: ignore[arg-type]
        opensubtitles=_NotConfigured(),  # type: ignore[arg-type]
        jellyfin=_NotConfigured(),  # type: ignore[arg-type]
    )

    async with session_factory() as s:
        result = await s.execute(select(Event).where(col(Event.type) == "system.warning"))
        warnings = result.scalars().all()
    assert warnings == []
