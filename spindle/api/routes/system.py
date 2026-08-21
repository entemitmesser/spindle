from __future__ import annotations

import asyncio
import shutil
import time

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlmodel import col

from spindle.__about__ import __version__
from spindle.config import ConfigStore, Settings
from spindle.models.event import Event
from spindle.models.types import utc_now
from spindle.schemas.system import (
    BackendStatus,
    DiskUsage,
    HealthResponse,
    SystemInfo,
    SystemWarning,
)
from spindle.services.backup import build_backup_zip

router = APIRouter()

_RECENT_WARNINGS_LIMIT = 20


@router.get("/health", operation_id="get_health")
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=time.monotonic() - request.app.state.started_at,
    )


@router.get("", operation_id="get_system_info")
async def system_info(request: Request) -> SystemInfo:
    settings: Settings = request.app.state.settings
    state = request.app.state

    backends: dict[str, BackendStatus] = {}
    encoders: list[str] = []
    if hasattr(state, "makemkv"):
        backends["makemkv"] = BackendStatus(available=await state.makemkv.is_available())
    if hasattr(state, "handbrake"):
        available = await state.handbrake.is_available()
        version = await state.handbrake.version() if available else None
        backends["handbrake"] = BackendStatus(available=available, version=version)
        if available:
            encoders = list(await state.handbrake.available_encoders())
    if hasattr(state, "mkvpropedit"):
        backends["mkvpropedit"] = BackendStatus(available=await state.mkvpropedit.is_available())

    disk: list[DiskUsage] = []
    for label, path in (
        ("work", settings.work_dir),
        ("library", settings.library_dir),
        ("data", settings.data_dir),
    ):
        if path.is_dir():
            usage = await asyncio.to_thread(shutil.disk_usage, path)
            disk.append(
                DiskUsage(
                    label=label,
                    path=str(path),
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                )
            )

    warnings: list[SystemWarning] = []
    if hasattr(state, "session_factory"):
        async with state.session_factory() as session:
            result = await session.execute(
                select(Event)
                .where(col(Event.type) == "system.warning")
                .order_by(col(Event.id).desc())
                .limit(_RECENT_WARNINGS_LIMIT)
            )
            for row in result.scalars().all():
                code = str(row.data.get("code", "unknown"))
                message = str(row.data.get("message", ""))
                warnings.append(SystemWarning(code=code, message=message))

    return SystemInfo(
        version=__version__,
        uptime_seconds=time.monotonic() - state.started_at,
        backends=backends,
        encoders=encoders,
        disk=disk,
        warnings=warnings,
    )


@router.post("/backup", operation_id="create_backup")
async def create_backup(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    config_store: ConfigStore = request.app.state.config_store
    runtime_config = await config_store.get()

    zip_bytes = await build_backup_zip(
        db_path=settings.data_dir / "spindle.db",
        runtime_config=runtime_config,
        presets_dir=settings.data_dir / "presets",
    )
    filename = f"spindle-backup-{utc_now():%Y%m%d-%H%M%S}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
