from __future__ import annotations

import time

from fastapi import APIRouter, Request

from spindle.__about__ import __version__
from spindle.schemas.system import HealthResponse, SystemInfo

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=time.monotonic() - request.app.state.started_at,
    )


@router.get("")
async def system_info(request: Request) -> SystemInfo:
    return SystemInfo(
        status="ok",
        version=__version__,
        uptime_seconds=time.monotonic() - request.app.state.started_at,
        encoders=[],
        backends=[],
        disk_space=[],
    )
