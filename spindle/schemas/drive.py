from __future__ import annotations

from pydantic import BaseModel

from spindle.models.drive import Drive, DriveStatus
from spindle.schemas.job import DiscOut, JobSummary


class DriveOut(BaseModel):
    id: int
    device_path: str
    display_name: str | None
    model: str | None
    status: DriveStatus
    is_enabled: bool
    disc: DiscOut | None = None
    job: JobSummary | None = None

    @classmethod
    def from_model(
        cls, drive: Drive, *, disc: DiscOut | None = None, job: JobSummary | None = None
    ) -> DriveOut:
        return cls(
            id=drive.id,  # type: ignore[arg-type]
            device_path=drive.device_path,
            display_name=drive.display_name,
            model=drive.model,
            status=drive.status,
            is_enabled=drive.is_enabled,
            disc=disc,
            job=job,
        )


class DrivePatch(BaseModel):
    is_enabled: bool | None = None
    display_name: str | None = None


class DrivesResponse(BaseModel):
    items: list[DriveOut]
