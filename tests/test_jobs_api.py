from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.models.job import JobStatus, MediaKind
from spindle.models.task import TaskKind, TaskStatus
from tests.factories import make_job, make_job_with_tasks


async def test_list_jobs_paginates_and_filters(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_job(session, title="Alpha", status=JobStatus.QUEUED)
    await make_job(session, title="Beta", status=JobStatus.COMPLETED)

    response = await client.get("/api/v1/jobs", params={"status": "queued"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Alpha"
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_list_jobs_title_substring_filter(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_job(session, title="The Matrix")
    await make_job(session, title="Inception")

    response = await client.get("/api/v1/jobs", params={"q": "matrix"})

    body = response.json()
    assert [item["title"] for item in body["items"]] == ["The Matrix"]


async def test_create_job_requires_existing_file(client: httpx.AsyncClient, tmp_path) -> None:
    response = await client.post("/api/v1/jobs", json={"source_path": str(tmp_path / "nope.mkv")})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed_error"


async def test_create_job_from_existing_file(client: httpx.AsyncClient, tmp_path) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"fake")

    response = await client.post(
        "/api/v1/jobs", json={"source_path": str(source), "title": "My Movie"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My Movie"
    assert body["status"] == "queued"
    assert len(body["tasks"]) == len(TaskKind)


async def test_get_job_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/jobs/999999")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found_error"
    assert "message" in body


async def test_get_job_returns_detail_with_progress(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job, tasks = await make_job_with_tasks(session, title="Dune", media_kind=MediaKind.MOVIE)
    rip_task = next(t for t in tasks if t.kind == TaskKind.IDENTIFY)
    rip_task.status = TaskStatus.SUCCEEDED
    session.add(rip_task)
    await session.commit()

    response = await client.get(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["current_task"] == "rip"
    assert body["progress"] > 0.0
    assert len(body["tasks"]) == len(TaskKind)


async def test_patch_job_rejects_when_not_mutable(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job = await make_job(session, status=JobStatus.RUNNING)

    response = await client.patch(f"/api/v1/jobs/{job.id}", json={"title": "New title"})

    assert response.status_code == 409


async def test_patch_job_updates_fields(client: httpx.AsyncClient, session: AsyncSession) -> None:
    job = await make_job(session, status=JobStatus.NEEDS_REVIEW)

    response = await client.patch(
        f"/api/v1/jobs/{job.id}", json={"title": "Fixed Title", "year": 1999, "tmdb_id": 42}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Fixed Title"
    assert body["year"] == 1999


async def test_retry_job_rejects_running(client: httpx.AsyncClient, session: AsyncSession) -> None:
    job, _tasks = await make_job_with_tasks(session, status=JobStatus.RUNNING)

    response = await client.post(f"/api/v1/jobs/{job.id}/retry")

    assert response.status_code == 409


async def test_retry_job_resets_from_stage(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job, tasks = await make_job_with_tasks(session, status=JobStatus.FAILED)
    for task in tasks:
        task.status = TaskStatus.SUCCEEDED
        task.attempts = 1
    rip_task = next(t for t in tasks if t.kind == TaskKind.RIP)
    rip_task.status = TaskStatus.FAILED
    rip_task.error = "boom"
    session.add_all(tasks)
    await session.commit()

    response = await client.post(f"/api/v1/jobs/{job.id}/retry", json={"from_stage": "rip"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    task_by_kind = {t["kind"]: t for t in body["tasks"]}
    assert task_by_kind["identify"]["status"] == "succeeded"
    assert task_by_kind["rip"]["status"] == "pending"
    assert task_by_kind["rip"]["attempts"] == 0
    assert task_by_kind["transcode"]["status"] == "pending"


async def test_cancel_job(client: httpx.AsyncClient, session: AsyncSession) -> None:
    job = await make_job(session, status=JobStatus.QUEUED)

    response = await client.post(f"/api/v1/jobs/{job.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_cancel_job_rejects_already_completed(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job = await make_job(session, status=JobStatus.COMPLETED)

    response = await client.post(f"/api/v1/jobs/{job.id}/cancel")

    assert response.status_code == 409


async def test_pause_and_resume_job(client: httpx.AsyncClient, session: AsyncSession) -> None:
    job = await make_job(session, status=JobStatus.RUNNING)

    paused = await client.post(f"/api/v1/jobs/{job.id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = await client.post(f"/api/v1/jobs/{job.id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"


async def test_resume_job_rejects_when_not_paused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job = await make_job(session, status=JobStatus.QUEUED)

    response = await client.post(f"/api/v1/jobs/{job.id}/resume")

    assert response.status_code == 409


async def test_delete_job_rejects_running_task(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job, tasks = await make_job_with_tasks(session, status=JobStatus.RUNNING)
    rip_task = next(t for t in tasks if t.kind == TaskKind.RIP)
    rip_task.status = TaskStatus.RUNNING
    session.add(rip_task)
    await session.commit()

    response = await client.delete(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 409


async def test_delete_job_removes_row(client: httpx.AsyncClient, session: AsyncSession) -> None:
    job, _tasks = await make_job_with_tasks(session, status=JobStatus.COMPLETED)

    response = await client.delete(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 204

    follow_up = await client.get(f"/api/v1/jobs/{job.id}")
    assert follow_up.status_code == 404


async def test_delete_job_purge_files_removes_work_dir(
    client: httpx.AsyncClient, session: AsyncSession, running_app
) -> None:
    job, _tasks = await make_job_with_tasks(session, status=JobStatus.COMPLETED)
    settings = running_app.state.settings
    raw_dir = settings.work_dir / "raw" / f"job-{job.id:06d}"
    raw_dir.mkdir(parents=True)
    (raw_dir / "title.mkv").write_bytes(b"data")

    response = await client.delete(f"/api/v1/jobs/{job.id}", params={"purge_files": "true"})

    assert response.status_code == 204
    assert not raw_dir.exists()


async def test_job_log_not_found_without_task(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job, _tasks = await make_job_with_tasks(session)

    response = await client.get(f"/api/v1/jobs/{job.id}/logs/rip")

    assert response.status_code == 404


async def test_job_log_returns_tail_and_range(
    client: httpx.AsyncClient, session: AsyncSession, running_app, tmp_path
) -> None:
    job, tasks = await make_job_with_tasks(session)
    rip_task = next(t for t in tasks if t.kind == TaskKind.RIP)
    log_path = tmp_path / "rip.log"
    log_path.write_bytes(b"line1\nline2\nline3\n")
    rip_task.log_path = str(log_path)
    session.add(rip_task)
    await session.commit()

    tail_response = await client.get(f"/api/v1/jobs/{job.id}/logs/rip", params={"tail": 2})
    assert tail_response.status_code == 200
    assert tail_response.text == "line2\nline3\n"

    range_response = await client.get(
        f"/api/v1/jobs/{job.id}/logs/rip", headers={"Range": "bytes=0-4"}
    )
    assert range_response.status_code == 206
    assert range_response.text == "line1"
    assert range_response.headers["Content-Range"] == "bytes 0-4/18"
