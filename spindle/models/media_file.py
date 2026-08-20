from __future__ import annotations

from typing import Any, Literal

from sqlmodel import CheckConstraint, Column, Field, SQLModel, String

from spindle.models.types import MutableJSON

MediaFileRole = Literal["source", "transcoded", "subtitle", "artwork"]


class MediaFile(SQLModel, table=True):
    __tablename__ = "media_file"
    __table_args__ = (
        CheckConstraint(
            "role IN ('source', 'transcoded', 'subtitle', 'artwork')", name="ck_media_file_role"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    path: str
    role: MediaFileRole = Field(sa_column=Column(String, nullable=False))
    size_bytes: int | None = None
    duration_seconds: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    language: str | None = None
    is_forced: bool = Field(default=False)
    meta: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableJSON, nullable=False)
    )
