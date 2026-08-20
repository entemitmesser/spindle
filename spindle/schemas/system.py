from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float


class SystemInfo(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    encoders: list[str] = []
    backends: list[str] = []
    disk_space: list[dict[str, object]] = []
