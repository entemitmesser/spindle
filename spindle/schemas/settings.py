from __future__ import annotations

from pydantic import BaseModel

from spindle.config import RuntimeConfig

# GET /api/v1/settings returns this directly. PUT accepts an arbitrary partial
# dict (a JSON merge patch) rather than a schema, since every field is optional
# and RuntimeConfig already validates the merged result.
SettingsResponse = RuntimeConfig


class PresetOut(BaseModel):
    name: str
    builtin: bool


class SettingsTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
