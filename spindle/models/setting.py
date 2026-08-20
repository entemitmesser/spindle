from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, Field, SQLModel

from spindle.models.types import MutableJSON, UTCDateTime, utc_now


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableJSON, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(UTCDateTime, nullable=False)
    )
