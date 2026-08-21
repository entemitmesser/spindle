from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float


class BackendStatus(BaseModel):
    available: bool
    version: str | None = None


class DiskUsage(BaseModel):
    label: str
    path: str
    total_bytes: int
    free_bytes: int


class SystemWarning(BaseModel):
    code: str
    message: str


class SystemInfo(BaseModel):
    version: str
    uptime_seconds: float
    backends: dict[str, BackendStatus] = {}
    encoders: list[str] = []
    disk: list[DiskUsage] = []
    warnings: list[SystemWarning] = []
