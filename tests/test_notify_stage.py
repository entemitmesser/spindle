from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.config import NotificationConfig, RuntimeConfig
from spindle.models.job import JobStatus
from spindle.models.task import TaskStatus
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import NotifyStage
from spindle.services.events import EventIn
from tests.factories import make_disc, make_drive, make_job


class _FakeNotifier:
    def __init__(self, *, sends: bool = True) -> None:
        self._sends = sends
        self.calls: list[dict[str, Any]] = []

    async def notify_job(self, job: Any, **kwargs: Any) -> bool:
        self.calls.append({"job": job, **kwargs})
        return self._sends


def _config(**notification_overrides: object) -> RuntimeConfig:
    return RuntimeConfig(notifications=NotificationConfig(**notification_overrides))  # type: ignore[arg-type]


async def _make_context(
    session: AsyncSession, tmp_path: Path, *, config: RuntimeConfig | None = None
) -> JobContext:
    drive = await make_drive(session)
    disc = await make_disc(session, drive_id=drive.id)
    job = await make_job(
        session,
        disc_id=disc.id,
        source_path=None,
        title="Amelie",
        year=2001,
        status=JobStatus.RUNNING,
    )
    await session.commit()

    async def emit(event: EventIn) -> None:
        return None

    return JobContext(
        job=job,
        disc=disc,
        drive=drive,
        work_dir=tmp_path,
        config=config or _config(),
        emit=emit,
        logger=structlog.get_logger(__name__),
    )


async def test_can_skip_is_always_false(session: AsyncSession, tmp_path: Path) -> None:
    ctx = await _make_context(session, tmp_path)
    stage = NotifyStage(_FakeNotifier())  # type: ignore[arg-type]
    assert await stage.can_skip(ctx) is False


async def test_run_sends_a_completed_notification_and_succeeds(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx = await _make_context(
        session, tmp_path, config=_config(enabled=True, webhook_url="http://x")
    )
    notifier = _FakeNotifier(sends=True)
    stage = NotifyStage(notifier)  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert notifier.calls[0]["event"] == "completed"


async def test_run_reads_the_organize_destination_from_job_meta(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx = await _make_context(session, tmp_path)
    ctx.job.meta = {"organize": {"destination": "/library/Movies/Amelie (2001)"}}
    notifier = _FakeNotifier(sends=True)
    stage = NotifyStage(notifier)  # type: ignore[arg-type]

    await stage.run(ctx)

    assert notifier.calls[0]["output_path"] == "/library/Movies/Amelie (2001)"


async def test_run_is_skipped_not_failed_when_notifications_are_off(
    session: AsyncSession, tmp_path: Path
) -> None:
    ctx = await _make_context(session, tmp_path, config=_config(enabled=False))
    stage = NotifyStage(_FakeNotifier(sends=False))  # type: ignore[arg-type]

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SKIPPED
