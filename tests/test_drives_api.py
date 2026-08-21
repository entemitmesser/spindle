from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.models.drive import DriveStatus
from spindle.models.job import JobStatus
from tests.factories import make_disc, make_drive, make_job


async def test_list_drives_includes_disc_and_job(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    drive = await make_drive(session, device_path="/dev/sr7", status=DriveStatus.READY)
    disc = await make_disc(session, drive_id=drive.id)
    job = await make_job(session, disc_id=disc.id, status=JobStatus.QUEUED, source_path=None)
    drive.current_job_id = job.id
    session.add(drive)
    await session.commit()

    response = await client.get("/api/v1/drives")

    assert response.status_code == 200
    items = response.json()["items"]
    entry = next(d for d in items if d["id"] == drive.id)
    assert entry["disc"]["id"] == disc.id
    assert entry["job"]["id"] == job.id


async def test_eject_drive_not_found(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/drives/999999/eject")

    assert response.status_code == 404


async def test_eject_drive_calls_adapter(
    client: httpx.AsyncClient, session: AsyncSession, running_app
) -> None:
    drive = await make_drive(session, device_path="/dev/sr8")

    calls: list[str] = []
    running_app.state.optical.eject = lambda device: calls.append(device)

    response = await client.post(f"/api/v1/drives/{drive.id}/eject")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == ["/dev/sr8"]


async def test_update_drive_patches_fields(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    drive = await make_drive(session, device_path="/dev/sr9")

    response = await client.patch(
        f"/api/v1/drives/{drive.id}", json={"is_enabled": False, "display_name": "Living Room"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_enabled"] is False
    assert body["display_name"] == "Living Room"


async def test_rescan_drive_resets_status(client: httpx.AsyncClient, session: AsyncSession) -> None:
    drive = await make_drive(session, device_path="/dev/sr10", status=DriveStatus.ERROR)

    response = await client.post(f"/api/v1/drives/{drive.id}/rescan")

    assert response.status_code == 200
    # No real drive_watcher wired (background workers disabled in tests), so
    # the status simply resets to empty pending the next real poll.
    assert response.json()["status"] == "empty"
