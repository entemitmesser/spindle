"""One-shot self-check run at process startup, per phase-09-hardening.md task
2: backends present, library writable, an external service referenced by
config but not configured. Never raises -- every finding becomes a
`system.warning` event (CLAUDE.md rule 7 applies to the whole app, not just
jobs), so it shows up in `GET /system` and the UI's warning banner instead of
failing the boot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from spindle.adapters.handbrake import HandBrakeAdapter
from spindle.adapters.jellyfin import JellyfinAdapter
from spindle.adapters.makemkv import MakeMkvAdapter
from spindle.adapters.mkvpropedit import MkvPropEditAdapter
from spindle.adapters.opensubtitles import OpenSubtitlesAdapter
from spindle.config import RuntimeConfig
from spindle.services.events import EventBus, EventIn

logger = structlog.get_logger(__name__)


def _dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".spindle_write_test"
        probe.touch()
        probe.unlink()
    except OSError:
        return False
    return True


async def run_startup_checks(
    *,
    event_bus: EventBus,
    config: RuntimeConfig,
    library_dir: Path,
    makemkv: MakeMkvAdapter,
    handbrake: HandBrakeAdapter,
    mkvpropedit: MkvPropEditAdapter,
    opensubtitles: OpenSubtitlesAdapter,
    jellyfin: JellyfinAdapter,
) -> None:
    warnings: list[tuple[str, str]] = []

    if not await makemkv.is_available() and not await handbrake.is_available():
        warnings.append(
            (
                "no_rip_backend",
                "Neither MakeMKV nor HandBrake was found -- discs can't be ripped "
                "until one is installed.",
            )
        )
    if not await mkvpropedit.is_available():
        warnings.append(
            (
                "mkvpropedit_unavailable",
                "mkvpropedit isn't installed -- container tagging will be skipped.",
            )
        )
    if not await asyncio.to_thread(_dir_is_writable, library_dir):
        warnings.append(
            (
                "library_not_writable",
                f"The library directory ({library_dir}) isn't writable -- "
                "organized files can't be moved there.",
            )
        )
    if (
        config.subtitles.enabled
        and config.subtitles.external_provider == "opensubtitles"
        and not opensubtitles.is_configured
    ):
        warnings.append(
            (
                "opensubtitles_not_configured",
                "Subtitles are enabled with OpenSubtitles as the provider, but no "
                "API key is configured.",
            )
        )
    if config.notifications.enabled and not config.notifications.webhook_url:
        warnings.append(
            ("notifications_not_configured", "Notifications are enabled but no webhook URL is set.")
        )
    if jellyfin.is_configured and not await jellyfin.ping():
        warnings.append(
            ("jellyfin_unreachable", "Jellyfin is configured but couldn't be reached at startup.")
        )

    for code, message in warnings:
        logger.warning("startup_check.warning", code=code)
        await event_bus.emit(
            EventIn(type="system.warning", data={"code": code, "message": message})
        )
