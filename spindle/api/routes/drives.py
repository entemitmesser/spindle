"""`/api/v1/drives`, per architecture.md §4."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.optical import OpticalAdapter
from spindle.errors import AdapterError, NotFoundError
from spindle.models.drive import Drive, DriveStatus
from spindle.schemas.drive import DriveOut, DrivePatch, DrivesResponse
from spindle.schemas.job import DiscOut, JobSummary
from spindle.services import repo
from spindle.services.detector import DriveWatcher

router = APIRouter()


async def _to_drive_out(session: AsyncSession, drive: Drive) -> DriveOut:
    disc = None
    if drive.status != DriveStatus.EMPTY:
        latest = await repo.get_latest_disc_for_drive(session, drive.id)  # type: ignore[arg-type]
        disc = DiscOut.from_model(latest) if latest is not None else None

    job = None
    if drive.current_job_id is not None:
        job_row = await repo.get_job(session, drive.current_job_id)
        if job_row is not None:
            tasks = await repo.get_tasks_for_job(session, job_row.id)  # type: ignore[arg-type]
            job = JobSummary.from_model(job_row, tasks, drive_id=drive.id)

    return DriveOut.from_model(drive, disc=disc, job=job)


@router.get("", operation_id="list_drives")
async def list_drives(request: Request) -> DrivesResponse:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        drives = await repo.get_drives(session)
        items = [await _to_drive_out(session, drive) for drive in drives]
    return DrivesResponse(items=items)


@router.post("/{drive_id}/eject", operation_id="eject_drive")
async def eject_drive(request: Request, drive_id: int) -> dict[str, bool]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        drive = await repo.get_drive(session, drive_id)
        if drive is None:
            raise NotFoundError(f"Drive {drive_id} not found.")
        device_path = drive.device_path

    optical: OpticalAdapter = request.app.state.optical
    try:
        await asyncio.to_thread(optical.eject, device_path)
    except AdapterError:
        return {"ok": False}
    return {"ok": True}


@router.post("/{drive_id}/rescan", operation_id="rescan_drive")
async def rescan_drive(request: Request, drive_id: int) -> DriveOut:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        drive = await repo.get_drive(session, drive_id)
        if drive is None:
            raise NotFoundError(f"Drive {drive_id} not found.")
        drive.status = DriveStatus.EMPTY
        await session.commit()

    drive_watcher: DriveWatcher | None = getattr(request.app.state, "drive_watcher", None)
    if drive_watcher is not None:
        await drive_watcher.poll_once()

    async with session_factory() as session:
        drive = await repo.get_drive(session, drive_id)
        assert drive is not None  # noqa: S101 -- checked above, row can't vanish mid-request
        return await _to_drive_out(session, drive)


@router.patch("/{drive_id}", operation_id="update_drive")
async def update_drive(request: Request, drive_id: int, body: DrivePatch) -> DriveOut:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        drive = await repo.get_drive(session, drive_id)
        if drive is None:
            raise NotFoundError(f"Drive {drive_id} not found.")
        if body.is_enabled is not None:
            drive.is_enabled = body.is_enabled
        if body.display_name is not None:
            drive.display_name = body.display_name
        await session.commit()
        await session.refresh(drive)
        return await _to_drive_out(session, drive)
