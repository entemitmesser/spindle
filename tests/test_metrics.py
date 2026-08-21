"""Phase 9 task 4: `GET /metrics` (Prometheus text format)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.main import create_app
from spindle.models.job import JobStatus
from spindle.models.task import TaskKind, TaskStatus
from spindle.models.types import utc_now
from spindle.services.metrics import render_prometheus_metrics
from tests.factories import make_job, make_task


async def test_render_prometheus_metrics_counts_jobs_and_tasks(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job1 = await make_job(session, status=JobStatus.COMPLETED)
    await make_job(session, status=JobStatus.FAILED)
    now = utc_now()
    await make_task(
        session,
        job_id=job1.id,
        kind=TaskKind.RIP,
        status=TaskStatus.SUCCEEDED,
        started_at=now - timedelta(seconds=30),
        finished_at=now,
    )
    await make_task(session, job_id=job1.id, kind=TaskKind.TRANSCODE, status=TaskStatus.FAILED)

    async with session_factory() as s:
        text = await render_prometheus_metrics(s)

    assert 'spindle_jobs_total{status="completed"} 1' in text
    assert 'spindle_jobs_total{status="failed"} 1' in text
    assert 'spindle_tasks_total{kind="rip",status="succeeded"} 1' in text
    assert 'spindle_task_errors_total{kind="transcode"} 1' in text
    assert 'spindle_task_duration_seconds_sum{kind="rip"} 30.000' in text
    assert 'spindle_task_duration_seconds_count{kind="rip"} 1' in text


async def test_metrics_endpoint_is_404_when_disabled(client: httpx.AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 404


async def test_metrics_endpoint_serves_prometheus_text_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A standalone app+client (not the shared `client` fixture) so this one
    # test's `SPINDLE_METRICS_ENABLED=true` can't leak into any other test in
    # this module via a same-named `app` fixture override.
    monkeypatch.setenv("SPINDLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPINDLE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SPINDLE_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SPINDLE_RUN_MIGRATIONS", "false")
    monkeypatch.setenv("SPINDLE_RUN_BACKGROUND_WORKERS", "false")
    monkeypatch.setenv("SPINDLE_METRICS_ENABLED", "true")

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")

    assert response.status_code == 200
    assert "spindle_jobs_total" in response.text
    assert response.headers["content-type"].startswith("text/plain")
