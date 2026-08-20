from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlmodel import Column, Field, SQLModel

from spindle.models.types import MutableJSON, UTCDateTime, utc_now


class DiscType(StrEnum):
    DVD = "dvd"
    BLURAY = "bluray"
    UHD_BLURAY = "uhd_bluray"
    AUDIO_CD = "audio_cd"
    DATA = "data"
    UNKNOWN = "unknown"


class Disc(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    drive_id: int = Field(foreign_key="drive.id", index=True)
    disc_type: DiscType = Field(default=DiscType.UNKNOWN)
    label: str | None = None
    volume_id: str | None = None
    fingerprint: str | None = Field(default=None, index=True)
    size_bytes: int | None = None
    meta: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableJSON, nullable=False)
    )
    detected_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
